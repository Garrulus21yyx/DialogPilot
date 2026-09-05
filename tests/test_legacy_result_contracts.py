"""Independent legacy result contracts still used by evaluation fixtures."""

import pytest

from agents.orchestration_contracts import AgentType, TaskPlan, TaskSpec
from services.result_synthesizer import AgentOutcome, AgentOutcomeStatus, CoverageGate


def outcome(
    agent_type: str,
    status=AgentOutcomeStatus.SUCCESS,
    *,
    primary=False,
    content="answer",
):
    return AgentOutcome(
        task_id=f"{agent_type}_task",
        required=True,
        agent_type=agent_type,
        responding_agent_type=agent_type,
        status=status,
        is_primary=primary,
        content=content if status is AgentOutcomeStatus.SUCCESS else "",
        error="failure" if status is not AgentOutcomeStatus.SUCCESS else "",
    )


def task_plan(*agent_types: AgentType) -> TaskPlan:
    """为融合器测试创建与 outcomes 一一对应的最小任务合同。"""
    tasks = tuple(
        TaskSpec(
            task_id=f"{agent_type.value}_task",
            owner=agent_type,
            objective=f"处理 {agent_type.value} 子任务",
        )
        for agent_type in agent_types
    )
    return TaskPlan(tasks=tasks, primary_task_id=tasks[0].task_id)


def test_coverage_gate_rejects_missing_required_task_even_when_one_agent_succeeds():
    """证明一个漂亮回答不能掩盖另一个必需子任务完全没有 outcome。"""
    plan = task_plan(AgentType.TECHNICAL, AgentType.BILLING)

    coverage = CoverageGate.evaluate(
        plan,
        [outcome("technical", primary=True, content="technical answer")],
    )

    assert coverage.complete is False
    assert coverage.completed_task_ids == ("technical_task",)
    assert coverage.missing_task_ids == ("billing_task",)
    assert coverage.unresolved_required_task_ids == ("billing_task",)


def test_coverage_gate_rejects_duplicate_outcome_for_the_same_task():
    """证明同一任务重复返回不会被误算成两项工作均已完成。"""
    plan = task_plan(AgentType.TECHNICAL)

    coverage = CoverageGate.evaluate(
        plan,
        [outcome("technical"), outcome("technical")],
    )

    assert coverage.complete is False
    assert coverage.duplicate_task_ids == ("technical_task",)


def test_task_graph_rejects_dangling_dependency_and_cycle_before_execution():
    """TaskGraph 在 Worker 调用前封闭悬空边与有向环。"""
    with pytest.raises(ValueError, match="dangling"):
        TaskPlan(
            tasks=(TaskSpec(
                "billing_task",
                AgentType.BILLING,
                "处理扣款",
                depends_on=("missing_task",),
            ),),
            primary_task_id="billing_task",
        )

    with pytest.raises(ValueError, match="cycle"):
        TaskPlan(
            tasks=(
                TaskSpec("technical_task", AgentType.TECHNICAL, "排障", depends_on=("billing_task",)),
                TaskSpec("billing_task", AgentType.BILLING, "核对", depends_on=("technical_task",)),
            ),
            primary_task_id="technical_task",
        )
