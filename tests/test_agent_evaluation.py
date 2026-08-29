"""Multi-Agent 计划、覆盖、预算与 fan-out 指标测试。"""

from types import SimpleNamespace

import pytest

from evaluation.evaluator import EndToEndEvaluator


def result(*, owners, coverage_complete=True, budget_failure=False):
    """构造不依赖模型客户端的最小编排评测投影。"""
    tasks = [
        {"task_id": f"{owner}_task", "owner": owner, "required": True}
        for owner in owners
    ]
    completed = [task["task_id"] for task in tasks] if coverage_complete else [tasks[0]["task_id"]]
    outcomes = [
        {
            "task_id": task["task_id"],
            "status": "budget_exceeded" if budget_failure and index == len(tasks) - 1 else "success",
        }
        for index, task in enumerate(tasks)
    ]
    return SimpleNamespace(
        task_plan={"tasks": tasks},
        coverage={
            "complete": coverage_complete,
            "required_task_ids": [task["task_id"] for task in tasks],
            "completed_task_ids": completed,
        },
        agent_outcomes=outcomes,
        agent_types=[],
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
