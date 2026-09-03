"""Target v1 adapter from governed WorkItems to the existing ToolManager."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.orchestration_runtime import AgentContextView
from application.work_item import ControlMode


_TOOL_AGENT = {
    "general": "general",
    "product_technical": "technical",
    "order_logistics": "general",
    "billing_refund": "billing",
    "account_security": "account_security",
    "human_service": "escalation",
}


class TargetToolExecutor:
    """Execute direct tools or registered skill tool sequences inside an envelope."""

    version = "target-tool-executor-v1"

    def __init__(self, tool_manager) -> None:
        self._tools = tool_manager

    async def __call__(self, context: AgentContextView) -> AgentResult:
        item = context.work_item
        if item.control_mode is ControlMode.WORKFLOW:
            return AgentResult(
                item.work_item_id,
                item.owner_agent,
                AgentResultStatus.TERMINAL_FAILURE,
                "WORKFLOW_REQUIRES_GOVERNED_WRITE_RUNTIME",
                self.version,
            )
        tool_ids = self._tool_sequence(context)
        facts = []
        evidence_refs = []
        rendered = []
        for index, tool_id in enumerate(tool_ids, start=1):
            params = {argument.name: argument.value for argument in item.arguments}
            if tool_id == "knowledge_search":
                params.setdefault("query", context.current_message)
            result = await self._tools.execute_for_agent(
                tool_id,
                params,
                agent_type=_TOOL_AGENT[item.owner_agent],
                context=dict(context.trusted_context),
                call_id=f"{item.work_item_id}:{index}:{tool_id}",
            )
            if not result.success:
                retryable = result.status in {"error", "timeout"}
                return AgentResult(
                    item.work_item_id,
                    item.owner_agent,
                    (
                        AgentResultStatus.RETRYABLE_FAILURE
                        if retryable else AgentResultStatus.TERMINAL_FAILURE
                    ),
                    f"TOOL_{str(result.status or 'FAILED').upper()}",
                    self.version,
                    retryable=retryable,
                )
            authority = str(result.authority or "")
            if authority in item.requirement_ids:
                value_json = json.dumps(
                    result.data,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                facts.append(FactRecord(
                    item.aggregate_ref or f"work-item:{item.work_item_id}",
                    authority,
                    value_json,
                    FactSourceKind.VERIFIED_STATE,
                    result.receipt_id or result.call_id,
                    tool_id,
                    str(result.output_schema_version or "tool-output-v1"),
                    datetime.now(timezone.utc),
                ))
            if result.receipt_id:
                evidence_refs.append(result.receipt_id)
            rendered.append(result.output_for_model or _render(result.data))
        satisfied = {fact.requirement_id for fact in facts}
        missing = set(item.requirement_ids).difference(satisfied)
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            (
                AgentResultStatus.SUCCEEDED
                if not missing else AgentResultStatus.TERMINAL_FAILURE
            ),
            "TOOL_REQUIREMENTS_SATISFIED" if not missing else "TOOL_REQUIREMENTS_MISSING",
            self.version,
            facts=tuple(facts),
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
            candidate_response="\n".join(item for item in rendered if item).strip() or None,
        )

    @staticmethod
    def _tool_sequence(context: AgentContextView) -> tuple[str, ...]:
        item = context.work_item
        if item.control_mode is ControlMode.DIRECT:
            if len(item.allowed_tools) != 1:
                raise ValueError("direct work must pin exactly one tool")
            return item.allowed_tools
        if item.skill_hint:
            # RoutePolicy already intersected the registered skill and agent envelopes.
            return item.allowed_tools
        if len(item.allowed_tools) == 1:
            return item.allowed_tools
        raise ValueError("delegated work requires an agent planner or a skill hint")


def _render(value: object) -> str:
    if isinstance(value, dict):
        for key in ("message", "answer", "status"):
            if str(value.get(key) or "").strip():
                return str(value[key])
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
