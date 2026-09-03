"""Execute the bounded single-tool read-only command-primary work shape."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agents.orchestration_contracts import AgentType, TaskEffect
from application.turn_plan import TurnPlan
from mcp.tool_manager import ToolExecutionReceipt, ToolResult


class ReadOnlyWorkError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadOnlyWorkExecution:
    task_id: str
    owner: AgentType
    response: str
    tool_result: ToolResult
    coverage: dict[str, Any]


async def execute_read_only_work(
    plan: TurnPlan,
    tool_manager: Any,
    identity: Any,
) -> ReadOnlyWorkExecution:
    work = plan.work
    if work is None or len(work.items) != 1 or len(work.graph.tasks) != 1:
        raise ReadOnlyWorkError("read-only primary execution requires one work item")
    item = work.items[0]
    task = work.graph.tasks[0]
    if task.effect is not TaskEffect.READ_ONLY or len(item.allowed_tools) != 1:
        raise ReadOnlyWorkError("work item is outside the read-only execution shape")

    tool_name = item.allowed_tools[0]
    params = {argument.name: argument.value for argument in item.arguments}
    result = await tool_manager.execute_for_agent(
        tool_name,
        params,
        agent_type=task.owner,
        context={
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
            "conv_id": str(identity.conversation_id),
            "request_id": str(identity.request_id),
            "invocation_key": str(identity.invocation_key),
        },
        call_id=f"{identity.request_id}:{item.task_id}",
    )
    receipt = ToolExecutionReceipt.from_result(result)
    return ReadOnlyWorkExecution(
        task_id=item.task_id,
        owner=task.owner,
        response=_render(result),
        tool_result=result,
        coverage={
            "complete": bool(result.success),
            "requirement_ids": list(task.requirement_ids),
            "tool_receipts": [receipt.to_dict()],
        },
    )


def _render(result: ToolResult) -> str:
    if not result.success:
        return "当前无法取得权威业务状态。"
    return "查询结果：" + json.dumps(
        result.data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
