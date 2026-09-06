"""Target v1 adapter from governed WorkItems to the existing ToolManager."""
from __future__ import annotations

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
)
from application.orchestration_runtime import AgentContextView
from application.work_item import ControlMode
from application.work_control import WorkControlGuard
from application.knowledge_tool_contract import tool_domain_outcome
from infrastructure.target_agent_result_adapter import fact_from_tool_result


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

    def __init__(self, tool_manager, *, control_guard: WorkControlGuard | None = None) -> None:
        self._tools = tool_manager
        self._control_guard = control_guard

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
        for index, tool_id in enumerate(tool_ids, start=1):
            if self._control_guard is not None:
                self._control_guard.ensure_current(item, context.trusted_context)
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
            if self._control_guard is not None:
                self._control_guard.ensure_current(item, context.trusted_context)
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
                    facts=tuple(facts), evidence_refs=tuple(dict.fromkeys(evidence_refs)),
                    retryable=retryable,
                )
            outcome = tool_domain_outcome(result)
            if outcome is not None and outcome[0] is not AgentResultStatus.SUCCEEDED:
                return AgentResult(
                    item.work_item_id, item.owner_agent, outcome[0], outcome[1], self.version,
                    facts=tuple(facts), evidence_refs=tuple(dict.fromkeys(evidence_refs)),
                    retryable=outcome[0] is AgentResultStatus.RETRYABLE_FAILURE,
                )
            authority = str(result.authority or "")
            if authority in item.requirement_ids:
                facts.append(fact_from_tool_result(item, result))
            if result.receipt_id:
                evidence_refs.append(result.receipt_id)
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
            # Tool payloads are facts for composition, not authored user replies.
            candidate_response=None,
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
