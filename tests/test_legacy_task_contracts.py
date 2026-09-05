"""TaskGraph dependency validation while legacy planning consumers remain."""

import pytest

from agents.orchestration_contracts import AgentType, TaskPlan, TaskSpec


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
