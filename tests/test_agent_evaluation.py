"""Multi-Agent 计划、覆盖、预算与 fan-out 指标测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from agents.orchestration_contracts import AgentType
from core.intent_recognizer import IntentCategory
from evaluation.evaluator import EndToEndEvaluator, QualityScores


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


def test_routing_evaluation_consumes_supplied_intent_instead_of_reclassifying():
    """Routing layer 的 gold intent/entities 必须直接进入 Planner 边界。"""
    captured = {}

    class Orchestrator:
        async def run(self, request):
            captured["request"] = request
            projection = result(owners=["technical"])
            return SimpleNamespace(
                **projection.__dict__,
                response="请按步骤排查 401。",
                agent_type=AgentType.TECHNICAL,
                intent=request.intent,
            )

    class Judge:
        async def judge(self, *_args, **_kwargs):
            return QualityScores(1.0, 1.0, 1.0, 1.0)

    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._orchestrator = Orchestrator()
    evaluator._judge = Judge()

    asyncio.run(evaluator._evaluate_dialog_case({
        "id": "routing-owner-contract",
        "question": "订单登录报 401 后重复扣款",
        "intent": "technical_login",
        "intent_confidence": 0.95,
        "entities": {"error_code": ["401"]},
        "expected_agents": ["technical"],
        "expected_task_ids": ["technical_task"],
    }, 0))

    request = captured["request"]
    assert request.intent is IntentCategory.TECHNICAL_LOGIN
    assert request.intent_confidence == 0.95
    assert request.entities == {"error_code": ["401"]}


def test_routing_layer_skips_answer_judge_and_accepts_empty_clarification_plan():
    """Routing 层只验证 owner/task 合同，澄清路径的空任务集是完整结果。"""
    class Orchestrator:
        async def run(self, request):
            return SimpleNamespace(
                response="请补充具体问题。",
                agent_type=AgentType.GENERAL,
                intent=request.intent,
                task_plan={},
                coverage={},
                agent_outcomes=[],
                agent_types=[AgentType.GENERAL],
            )

    class Judge:
        async def judge(self, *_args, **_kwargs):
            raise AssertionError("routing-only evaluation must not call the answer judge")

    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._orchestrator = Orchestrator()
    evaluator._judge = Judge()

    results = asyncio.run(evaluator._evaluate_dialog_case({
        "id": "routing-clarification-contract",
        "question": "帮我看看这个问题",
        "intent": "other",
        "intent_confidence": 0.2,
        "entities": {},
        "expected_agents": ["general"],
        "expected_task_ids": [],
        "evaluation_layer": "routing",
    }, 0))

    assert results[0].passed is True
    assert results[0].scores["coverage_complete"] == 1.0
    assert results[0].scores["task_coverage"] == 1.0
    assert "overall" not in results[0].scores
