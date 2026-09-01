"""评测 Rubric、不可变 Baseline 与 Graduation Gate 的正向合同。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agents.orchestration_contracts import AgentType
from application.chat_application import Completed
from core.intent_recognizer import IntentCategory
from evaluation.evaluator import EndToEndEvaluator, EvalReport, EvalResult, QualityScores
from evaluation.chat_application_runner import ChatApplicationRunner
from evaluation.graduation import (
    GraduationDecision,
    GraduationEvidence,
    GraduationGate,
    GraduationStatus,
    ImmutableBaselineStore,
)
from evaluation.rubric import CaseRubric


HARD_GATES = {
    "security": True,
    "identity_isolation": True,
    "tool_authorization": True,
    "coverage": True,
    "stateful": True,
}


def _chat_runner(response: str, *, agent_type: str, intent: str):
    class Application:
        async def handle(self, _command):
            return Completed(response_id="eval-response", response={
                "response": response,
                "coverage": {
                    "complete": True,
                    "required_task_ids": [],
                    "completed_task_ids": [],
                },
                "agent_outcomes": [],
                "task_plan": {"tasks": []},
                "agent_types": [agent_type],
                "agent_type": agent_type,
                "intent": intent,
                "routing_disposition": "execute",
                "tool_audit": [],
            })

    return ChatApplicationRunner(lambda _overrides: Application())


def _report(*, pass_rate: float = 1.0, regressions=None, judge_failed: bool = False):
    result = EvalResult(
        test_id="case-1",
        passed=pass_rate == 1.0,
        scores={"accuracy": pass_rate},
        metadata={"judge_failed": judge_failed},
    )
    return EvalReport(
        timestamp="2026-08-31T00:00:00+00:00",
        total=1,
        passed=1 if pass_rate == 1.0 else 0,
        pass_rate=pass_rate,
        avg_scores={"accuracy": pass_rate},
        regressions=list(regressions or []),
        recommendations=[],
        results=[result],
    )


def _evidence(report=None, **overrides):
    values = {
        "candidate_id": "agent-v2",
        "report": report or _report(),
        "hard_gates": HARD_GATES,
        "review_status": "human_reviewed",
        "fresh_heldout": True,
        "heldout_evidence_id": "heldout-v2",
        "heldout_checksum": "a" * 64,
        "latency_ratio": 1.0,
        "cost_ratio": 1.0,
    }
    values.update(overrides)
    return GraduationEvidence(**values)


def test_graduation_hard_failure_cannot_be_offset_by_quality_score():
    """安全门禁失败时，即使质量分满分也必须拒绝晋级。"""
    gates = dict(HARD_GATES)
    gates["tool_authorization"] = False

    decision = GraduationGate().evaluate(_evidence(hard_gates=gates))

    assert decision.status is GraduationStatus.REJECTED
    assert "tool_authorization" in " ".join(decision.reasons)


def test_graduation_missing_fresh_provenance_is_insufficient_not_success():
    """仅声称跑过 heldout 不够，还需要封存标识和 checksum。"""
    decision = GraduationGate().evaluate(_evidence(
        heldout_evidence_id="",
        heldout_checksum="",
    ))

    assert decision.status is GraduationStatus.INSUFFICIENT_EVIDENCE
    assert any("checksum" in reason for reason in decision.reasons)


def test_immutable_baseline_store_appends_snapshots_and_switches_pointer(tmp_path):
    """晋级只追加快照并切换指针，旧报告不被覆盖。"""
    store = ImmutableBaselineStore(tmp_path / "baseline.json")
    first_report = _report()
    first_decision = GraduationDecision(GraduationStatus.GRADUATED, "agent-v1")
    first = store.promote(report=first_report, decision=first_decision, actor="reviewer")

    second_report = _report(pass_rate=0.9)
    second_decision = GraduationDecision(GraduationStatus.GRADUATED, "agent-v2")
    second = store.promote(report=second_report, decision=second_decision, actor="reviewer")

    persisted = json.loads((tmp_path / "baseline.json").read_text(encoding="utf-8"))
    assert first.snapshot_id != second.snapshot_id
    assert persisted["active_snapshot_id"] == second.snapshot_id
    assert len(persisted["snapshots"]) == 2
    by_id = {item["snapshot_id"]: item for item in persisted["snapshots"]}
    assert by_id[first.snapshot_id]["report"]["pass_rate"] == 1.0
    assert store.active().candidate_id == "agent-v2"


def test_non_graduated_candidate_cannot_enter_baseline_store(tmp_path):
    store = ImmutableBaselineStore(tmp_path / "baseline.json")
    rejected = GraduationDecision(GraduationStatus.REJECTED, "agent-bad")

    try:
        store.promote(report=_report(), decision=rejected, actor="reviewer")
    except ValueError as exc:
        assert "graduated" in str(exc)
    else:
        raise AssertionError("rejected candidate unexpectedly became baseline")
    assert not (tmp_path / "baseline.json").exists()


def test_eval_run_does_not_create_or_overwrite_baseline(tmp_path):
    """普通评测只产生报告，不具备修改 Active Baseline 的权威。"""
    class Orchestrator:
        async def run(self, _request):
            return SimpleNamespace(
                response="请核对订单号后申请退款。",
                coverage={"complete": True, "required_task_ids": [], "completed_task_ids": []},
                agent_outcomes=[],
                task_plan={"tasks": []},
                agent_types=[AgentType.GENERAL],
                agent_type=AgentType.GENERAL,
                intent=IntentCategory.REFUND,
            )

    class Judge:
        async def judge(self, *_args, **_kwargs):
            return QualityScores(1.0, 1.0, 1.0, 1.0)

    baseline_path = tmp_path / "baseline.json"
    evaluator = EndToEndEvaluator(
        orchestrator=Orchestrator(),
        recognizer=SimpleNamespace(),
        api_key="test-key",
        baseline_path=str(baseline_path),
        chat_runner=_chat_runner(
            "请核对订单号后申请退款。",
            agent_type=AgentType.GENERAL.value,
            intent=IntentCategory.REFUND.value,
        ),
    )
    evaluator._judge = Judge()

    report = asyncio.run(evaluator.run(dialog_cases=[{"question": "怎么退款？"}]))

    assert report.pass_rate == 1.0
    assert evaluator.history[-1] is report
    assert not baseline_path.exists()


def test_case_rubric_hard_failure_and_dimension_floor_both_block_pass():
    """硬禁词和逐维最低分均是门禁，不能被 overall 平均分抵消。"""
    class Orchestrator:
        async def run(self, _request):
            return SimpleNamespace(
                response="退款已经必然成功。",
                coverage={"complete": True, "required_task_ids": [], "completed_task_ids": []},
                agent_outcomes=[],
                task_plan={"tasks": []},
                agent_types=[AgentType.BILLING],
                agent_type=AgentType.BILLING,
                intent=IntentCategory.REFUND,
            )

    class Judge:
        async def judge(self, *_args, **_kwargs):
            return QualityScores(1.0, 0.85, 1.0, 1.0)

    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._orchestrator = Orchestrator()
    evaluator._chat_runner = _chat_runner(
        "退款已经必然成功。",
        agent_type=AgentType.BILLING.value,
        intent=IntentCategory.REFUND.value,
    )
    evaluator._judge = Judge()
    results = asyncio.run(evaluator._evaluate_dialog_case({
        "id": "rubric-hard-gate",
        "question": "我的退款成功了吗？",
        "rubric": {
            "id": "refund-evidence",
            "forbidden_phrases": ["必然成功"],
            "min_dimension_scores": {"accuracy": 0.9},
            "semantic_criteria": ["必须区分申请与入账结果"],
        },
    }, 0))

    assert results[0].passed is False
    assert results[0].scores["overall"] > 0.75
    assert results[0].scores["rubric_hard_pass"] == 0.0
    assert results[0].metadata["rubric"]["hard_pass"] is False


def test_regression_comparison_uses_active_baseline_not_previous_run():
    """进程内上一轮只是历史，不能取代 Active Baseline 的权威。"""
    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._baseline = _report(pass_rate=1.0)
    evaluator._baseline.avg_scores = {"accuracy": 1.0}
    evaluator._history = [_report(pass_rate=0.4)]
    evaluator._history[0].avg_scores = {"accuracy": 0.4}

    regressions = evaluator._detect_regressions({"accuracy": 0.8})

    assert len(regressions) == 1
    assert "1.000" in regressions[0]


def test_case_rubric_normalizes_unicode_before_matching():
    rubric = CaseRubric.from_mapping({"must_include_all": ["ＡＢＣ"]})

    assert rubric.evaluate("abc").hard_pass is True
