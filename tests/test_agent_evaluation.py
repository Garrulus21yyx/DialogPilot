"""Multi-Agent 计划、覆盖、预算与 fan-out 指标测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from evaluation.evaluator import EndToEndEvaluator, QualityScores
from application.capability_registry import CapabilityRisk
from application.turn_planning import RouteDecision, RouteMode, TurnPlan


def result(*, owners, coverage_complete=True, budget_failure=False):
    """构造不依赖模型客户端的最小编排评测投影。"""
    tasks = [
        {"task_id": f"{owner}_task", "owner": owner, "required": True}
        for owner in owners
    ]
    completed = [task["task_id"] for task in tasks] if coverage_complete else [tasks[0]["task_id"]]
    outcomes = [
        {
            "work_item_id": task["task_id"],
            "status": "SUCCEEDED" if task["task_id"] in completed else "TERMINAL_FAILURE",
            "reason_code": "AGENT_STEP_BUDGET_EXCEEDED" if budget_failure and index == len(tasks) - 1 else "DONE",
        }
        for index, task in enumerate(tasks)
    ]
    return SimpleNamespace(
        task_plan={"work_item_ids": [task["task_id"] for task in tasks]},
        coverage={
            "complete": coverage_complete,
            "required_task_ids": [task["task_id"] for task in tasks],
            "completed_task_ids": completed,
        },
        agent_outcomes=outcomes,
        agent_types=owners,
    )


def test_orchestration_scores_reward_exact_owner_set_and_complete_tasks():
    """证明正确 Owner 集合和全任务覆盖能产生满分编排指标。"""
    scores = EndToEndEvaluator._orchestration_scores(
        result(owners=["technical", "billing"]),
        {
            "expected_agents": ["technical", "billing"],
            "expected_task_ids": ["technical_task", "billing_task"],
        },
    )

    assert scores["route_exact_match"] == 1.0
    assert scores["route_jaccard"] == 1.0
    assert scores["task_exact_match"] == 1.0
    assert scores["coverage_complete"] == 1.0
    assert scores["task_coverage"] == 1.0
    assert scores["fanout_efficiency"] == 1.0
    assert scores["budget_success_rate"] == 1.0


def test_orchestration_scores_expose_extra_agent_missing_coverage_and_budget_failure():
    """证明无效 fan-out、覆盖缺口和预算失败不会被最终文本分数掩盖。"""
    scores = EndToEndEvaluator._orchestration_scores(
        result(
            owners=["account_security", "billing", "technical"],
            coverage_complete=False,
            budget_failure=True,
        ),
        {"expected_agents": ["account_security", "billing"]},
    )

    assert scores["route_exact_match"] == 0.0
    assert scores["route_jaccard"] == pytest.approx(2 / 3)
    assert scores["coverage_complete"] == 0.0
    assert scores["task_coverage"] == pytest.approx(1 / 3)
    assert scores["fanout_efficiency"] == pytest.approx(2 / 3)
    assert scores["budget_success_rate"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("mode", [RouteMode.CLARIFY, RouteMode.OUT_OF_SCOPE])
def test_target_terminal_plans_need_no_workers_or_answer_judge(mode):
    plan = TurnPlan(
        RouteDecision(mode, (), (), CapabilityRisk.LOW, (), "TEST"),
        None, None, "state", "registry", "compiler",
    )

    class Planner:
        async def plan(self, command, *, history, entities):
            return plan

    class Judge:
        async def judge(self, *_args, **_kwargs):
            raise AssertionError("planner-only evaluation must not judge a nonexistent answer")

    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._planning_runner_factory = Planner
    evaluator._judge = Judge()
    results = asyncio.run(evaluator._evaluate_dialog_case({
        "question": "帮我看看", "evaluation_layer": "routing",
        "expected_agents": [], "expected_task_ids": [],
        "expected_disposition": mode.value.lower(),
    }, 0))
    assert results[0].passed
    assert results[0].metadata["routing_disposition"] == mode.value.lower()
    assert results[0].metadata["agent_outcomes"] == []
    assert "overall" not in results[0].scores


def test_target_missing_coverage_cannot_be_overridden_by_empty_gold_tasks():
    scores = EndToEndEvaluator._orchestration_scores(
        SimpleNamespace(task_plan={"work_item_ids": []}, coverage={"complete": False},
                        agent_outcomes=[], agent_types=[]),
        {"expected_agents": [], "expected_task_ids": []},
    )
    assert scores["coverage_complete"] == 0
    assert scores["task_coverage"] == 0


def test_verified_no_work_terminal_has_full_coverage_and_no_unnecessary_fanout():
    scores = EndToEndEvaluator._orchestration_scores(
        SimpleNamespace(task_plan={"work_item_ids": []}, coverage={"complete": True},
                        agent_outcomes=[], agent_types=[]),
        {"expected_agents": [], "expected_task_ids": []},
    )
    assert all(value == 1 for value in scores.values())
