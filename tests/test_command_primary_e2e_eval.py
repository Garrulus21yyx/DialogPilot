"""The E2E evaluator records one proven command-primary chat contract."""
from __future__ import annotations

import asyncio
import json

from evaluation.chat_application_runner import ChatApplicationRunner
from evaluation.command_primary_eval.contracts import EvalCase, EvaluationStatus
from evaluation.command_primary_eval.e2e import ChatApplicationE2EAdapter
from evaluation.command_primary_eval.e2e_runner import ChatApplicationE2ERunner
from tests.support.sticky_read_only_harness import build_sticky_read_only_harness


def test_sticky_read_only_e2e_writes_four_outcome_dimensions(tmp_path, customer_operations) -> None:
    harness = build_sticky_read_only_harness(customer_operations)
    chat_runner = ChatApplicationRunner(
        harness.application_factory,
        state_probes={
            "flow_state": harness.flow_state_probe,
            "tool_side_effects": harness.tool_side_effects_probe,
        },
    )
    case = EvalCase(
        case_id="refund-continuation-001",
        message="还是没到账",
        initial_state={
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "conversation_id": "conversation-1",
            "request_id": "request-1",
        },
        expected={
            "user_outcome": {
                "outcome_type": "Completed",
                "intent": "refund",
                "routing_disposition": "execute",
                "coverage_complete": True,
                "response_contains": ['"status":"requested"'],
            },
            "flow_transition": {
                "stage_status": "ok",
                "stage_detail": {"status": "applied"},
                "owner_state": {
                    "aggregate_version": 2,
                    "flow_id": "refund_status",
                    "flow_state_version": 4,
                },
            },
            "component_invocations": {
                "legacy_intent": "SKIPPED",
                "semantic_router": "INVOKED",
                "knowledge_rag": "SKIPPED",
                "business_tool": "INVOKED",
            },
            "tool_side_effects": {
                "calls": [{
                    "tool_name": "refund_status",
                    "status": "success",
                    "read_only": True,
                    "effect_status": "none",
                }],
            },
        },
        slice="sticky_read_only",
    )

    report = asyncio.run(ChatApplicationE2ERunner(
        ChatApplicationE2EAdapter(chat_runner),
    ).run(
        (case,),
        tmp_path / "result",
        run_id="sticky-read-only-run-001",
        dataset_id="synthetic-contract-v1",
        split="dev",
        configuration={"route_policy": "command-primary"},
    ))

    assert report.status is EvaluationStatus.PASS
    result_dir = tmp_path / "result"
    assert sorted(path.name for path in result_dir.iterdir()) == [
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    ]
    manifest = json.loads((result_dir / "manifest.json").read_text())
    prediction = json.loads((result_dir / "predictions.jsonl").read_text())
    persisted_report = json.loads((result_dir / "report.json").read_text())

    assert manifest["component"] == "chat_application_e2e"
    assert prediction["status"] == "PASS"
    assert prediction["trigger"]["passed"] is True
    assert prediction["artifact"]["passed"] is True
    assert prediction["consumption"]["passed"] is True
    assert prediction["outcome"]["passed"] is True
    assert persisted_report["passed"] == 1
