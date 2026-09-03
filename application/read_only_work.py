"""Execute one Registry-compiled read-only work item."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agents.orchestration_contracts import AgentType, TaskEffect
from application.authority_policy import AuthorityPolicyRegistry, FactRequirement
from application.business_read_evidence import build_business_read_evidence
from application.evidence_receipt import EvidenceReceipt
from application.turn_plan import TurnPlan
from mcp.tool_manager import ToolExecutionReceipt, ToolResult


class ReadOnlyWorkError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReadOnlyWorkExecution:
    task_id: str
    owner: AgentType
    response: str
    tool_results: tuple[ToolResult, ...]
    tool_receipts: tuple[ToolExecutionReceipt, ...]
    evidence_receipts: tuple[EvidenceReceipt, ...]
    coverage: dict[str, Any]

    @property
    def succeeded(self) -> bool:
        return (
            bool(self.tool_results)
            and all(item.success for item in self.tool_results)
            and bool(self.coverage.get("complete"))
        )


async def execute_read_only_work(
    plan: TurnPlan,
    tool_manager: Any,
    identity: Any,
    requirements: tuple[FactRequirement, ...],
) -> ReadOnlyWorkExecution:
    work = plan.work
    if work is None or len(work.items) != 1 or len(work.graph.tasks) != 1:
        raise ReadOnlyWorkError("read-only primary execution requires one work item")
    item = work.items[0]
    task = work.graph.tasks[0]
    if task.effect is not TaskEffect.READ_ONLY:
        raise ReadOnlyWorkError("work item is outside the read-only execution shape")

    requirement_by_id = {value.requirement_id: value for value in requirements}
    try:
        ordered_requirements = tuple(
            requirement_by_id[value] for value in task.requirement_ids
        )
    except KeyError as exc:
        raise ReadOnlyWorkError("work requirement was not resolved") from exc
    tool_names = tuple(value.required_tool for value in ordered_requirements)
    if not tool_names or any(value is None for value in tool_names):
        raise ReadOnlyWorkError("read-only requirement needs one authorized producer")
    if any(value not in item.allowed_tools for value in tool_names):
        raise ReadOnlyWorkError("required producer exceeds the action permission set")

    policies = AuthorityPolicyRegistry.v1()
    manifests = {value.name: value for value in tool_manager.registered_tools}
    for tool_name in tool_names:
        tool = manifests.get(tool_name)
        if tool is None or not tool.read_only:
            raise ReadOnlyWorkError("required read-only producer is unavailable")
        policies.validate_tool_manifest(tool)

    params = {argument.name: argument.value for argument in item.arguments}
    tool_results: list[ToolResult] = []
    tool_receipts: list[ToolExecutionReceipt] = []
    for index, tool_name in enumerate(tool_names, start=1):
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
            call_id=f"{identity.request_id}:{item.task_id}:{index}",
        )
        tool_results.append(result)
        tool_receipts.append(ToolExecutionReceipt.from_result(result))
        if not result.success:
            break

    observed_at = datetime.now(timezone.utc)
    evidence = build_business_read_evidence(
        ordered_requirements,
        tuple(tool_results),
        arguments=params,
        observed_at=observed_at,
    )
    coverage = evidence.coverage.to_dict()
    coverage["tool_receipts"] = [value.to_dict() for value in tool_receipts]
    coverage["evidence_receipts"] = [
        value.to_dict() for value in evidence.receipts
    ]
    return ReadOnlyWorkExecution(
        task_id=item.task_id,
        owner=task.owner,
        response=_render(tuple(tool_results)),
        tool_results=tuple(tool_results),
        tool_receipts=tuple(tool_receipts),
        evidence_receipts=evidence.receipts,
        coverage=coverage,
    )


def _render(results: tuple[ToolResult, ...]) -> str:
    if not results or not all(result.success for result in results):
        return "当前无法取得完整的权威业务状态。"
    return "查询结果：" + json.dumps(
        {result.tool_name: result.data for result in results},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
