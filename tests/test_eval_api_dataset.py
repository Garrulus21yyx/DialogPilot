"""注册评测集到 HTTP 运行边界的合同测试。"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import main
from core.auth import Principal
from evaluation.dataset import write_dataset
from evaluation.graduation import GraduationDecision, GraduationStatus


ADMIN = Principal(subject="test-admin", scopes=frozenset({"admin"}))


def _source():
    return {"dataset": "test", "license": "project-private"}


def _row(case_id, layer, expected, *, status="human_reviewed"):
    review = {"status": status, "reviewer": "test"}
    if status == "human_reviewed":
        review.update({
            "reviewed_at": "2026-08-30T00:00:00+00:00",
            "notes": "test fixture",
        })
    return {
        "schema_version": 1,
        "id": case_id,
        "layer": layer,
        "split": "dev",
        "group_id": case_id,
        "input": {
            "message": f"message for {case_id}",
            **({
                "intent": "technical_login",
                "intent_confidence": 0.93,
                "entities": {"error_code": ["401"]},
            } if layer == "routing" else {}),
        },
        "expected": expected,
        "tags": [],
        "source": _source(),
        "review": review,
    }


def _registry(tmp_path, monkeypatch, *, status="human_reviewed"):
    registry = tmp_path / "registry"
    write_dataset(
        registry / "project-v1",
        manifest={
            "dataset_id": "project",
            "version": "1.0.0",
            "status": "test",
            "sources": [_source()],
        },
        cases=[
            _row("intent-1", "intent", {"intent": "greeting"}, status=status),
            _row(
                "route-1",
                "routing",
                {"owners": ["technical", "billing"], "task_ids": ["technical_task", "billing_task"]},
                status=status,
            ),
        ],
    )
    monkeypatch.setenv("EVAL_DATASET_DIR", str(registry))


def test_registered_dataset_converts_to_runtime_cases_and_metadata(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)

    intent_cases, dialog_cases, metadata = main._registered_eval_inputs(
        main.EvalRunInput(dataset_id="project-v1", split="dev")
    )

    assert intent_cases[0].expected_intent == "greeting"
    assert dialog_cases[0]["id"] == "route-1"
    assert dialog_cases[0]["expected_agents"] == ["technical", "billing"]
    assert dialog_cases[0]["intent"] == "technical_login"
    assert dialog_cases[0]["intent_confidence"] == 0.93
    assert dialog_cases[0]["entities"] == {"error_code": ["401"]}
    assert dialog_cases[0]["evaluation_layer"] == "routing"
    assert metadata["dataset_version"] == "1.0.0"
    assert len(metadata["dataset_checksum"]) == 64
    assert metadata["review_scope"] == "human_reviewed"


def test_registered_dataset_defaults_to_gold_and_requires_explicit_draft_opt_in(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, status="provisional")

    with pytest.raises(HTTPException) as exc:
        main._registered_eval_inputs(main.EvalRunInput(dataset_id="project-v1"))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "no_eligible_eval_cases"

    intent_cases, dialog_cases, metadata = main._registered_eval_inputs(
        main.EvalRunInput(dataset_id="project-v1", include_non_gold=True)
    )
    assert len(intent_cases) == 1
    assert len(dialog_cases) == 1
    assert metadata["review_scope"] == "all"


def test_runtime_rejects_prediction_protocol_layers(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        main._registered_eval_inputs(
            main.EvalRunInput(dataset_id="project-v1", layers=["retrieval"])
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "runtime_layer_unsupported"


def test_eval_run_forwards_dataset_identity_to_report(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)

    class FakeEvaluator:
        async def run(self, **kwargs):
            return SimpleNamespace(
                pass_rate=1.0,
                total=2,
                passed=2,
                avg_scores={},
                regressions=[],
                recommendations=[],
                results=[],
                metadata=kwargs["metadata"],
            )

    monkeypatch.setattr(main, "_evaluator", FakeEvaluator())
    response = asyncio.run(
        main.run_eval(main.EvalRunInput(dataset_id="project-v1"), ADMIN)
    )

    assert response["metadata"]["registry_id"] == "project-v1"
    assert response["metadata"]["case_count"] == 2


def test_graduation_check_is_read_only_and_promotion_uses_admin_identity(monkeypatch):
    captured = {}

    class FakeSnapshot:
        def to_dict(self):
            return {"snapshot_id": "sha", "candidate_id": "agent-v2"}

    class FakeEvaluator:
        baseline_snapshot = None

        def assess_latest_candidate(self, **kwargs):
            captured["check"] = kwargs
            return GraduationDecision(GraduationStatus.GRADUATED, kwargs["candidate_id"])

        def promote_latest_candidate(self, **kwargs):
            captured["promote"] = kwargs
            return (
                GraduationDecision(GraduationStatus.GRADUATED, kwargs["candidate_id"]),
                FakeSnapshot(),
            )

    body = main.EvalGraduationInput(
        candidate_id="agent-v2",
        hard_gates={
            "security": True,
            "identity_isolation": True,
            "tool_authorization": True,
            "coverage": True,
            "stateful": True,
        },
        review_status="human_reviewed",
        fresh_heldout=True,
        heldout_evidence_id="heldout-v2",
        heldout_checksum="a" * 64,
    )
    monkeypatch.setattr(main, "_evaluator", FakeEvaluator())

    checked = asyncio.run(main.check_eval_graduation(body, ADMIN))
    promoted = asyncio.run(main.promote_eval_baseline(body, ADMIN))

    assert checked["active_changed"] is False
    assert promoted["active_changed"] is True
    assert captured["promote"]["actor"] == "test-admin"
    assert captured["promote"]["heldout_checksum"] == "a" * 64
