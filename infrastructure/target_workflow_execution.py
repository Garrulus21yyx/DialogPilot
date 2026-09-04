"""Target v1 workflow executor for governed ToolManager business writes."""
from __future__ import annotations

from application.agent_result import AgentResult, AgentResultStatus
from application.capability_registry import ApprovalPolicy
from application.conversation_store import ConversationScope
from application.orchestration_runtime import AgentContextView
from application.write_workflow import (
    ApprovalGrant,
    GovernedWriteRuntime,
    WriteOutcomeStatus,
    WriteToolOutcome,
)
from core.identity import ConversationId, TenantId, UserId
from infrastructure.postgres_target_runtime import PostgresOperationLedger


_AGENT_TYPE = {
    "billing_refund": "billing",
    "human_service": "escalation",
}


class TargetWorkflowExecutor:
    """Bind each write to its conversation ledger and registry approval mode."""

    version = "target-workflow-executor-v1"

    def __init__(self, pool, tool_manager) -> None:
        self._pool = pool
        self._tools = tool_manager

    async def __call__(self, context: AgentContextView) -> AgentResult:
        item = context.work_item
        trusted = context.trusted_context
        scope = ConversationScope(
            TenantId(trusted["tenant_id"]),
            UserId(trusted["user_id"]),
            ConversationId(trusted["conversation_id"]),
        )
        grants = self._approval_grants(item, trusted)
        runtime = GovernedWriteRuntime(
            ledger=PostgresOperationLedger(self._pool, scope),
            tool_port=_ToolPort(self._tools, context),
            reconciliation_port=_ToolReconciler(self._tools, context),
            approval_grants=grants,
        )
        return await runtime(context)

    @staticmethod
    def _approval_grants(item, trusted):
        grants = {}
        if item.approval_policy is ApprovalPolicy.USER_COMMAND_SUFFICIENT:
            grants[str(item.approval_binding)] = ApprovalGrant(
                str(item.approval_binding),
                str(item.operation_key),
                str(item.target_entity_version),
                True,
                f"user-command:{trusted['request_id']}",
            )
        elif (
            item.approval_policy is ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED
            and
            trusted.get("approved_operation_key") == item.operation_key
            and trusted.get("approval_binding") == item.approval_binding
            and trusted.get("approval_target_version") == item.target_entity_version
        ):
            grants[str(item.approval_binding)] = ApprovalGrant(
                str(item.approval_binding),
                str(item.operation_key),
                str(item.target_entity_version),
                True,
                str(trusted.get("approval_actor") or trusted["user_id"]),
            )
        return grants


class _ToolPort:
    def __init__(self, tool_manager, context: AgentContextView) -> None:
        self._tools = tool_manager
        self._context = context

    async def execute(self, item, *, tool_id, arguments, operation_key):
        context = {
            **dict(self._context.trusted_context),
            "business_operation_key": operation_key,
        }
        result = await self._tools.execute_for_agent(
            tool_id,
            dict(arguments),
            agent_type=_AGENT_TYPE[item.owner_agent],
            context=context,
            approved=True,
            call_id=operation_key,
        )
        if result.success and str(result.effect_status).lower() == "committed":
            receipt_schema = str(
                getattr(result, "receipt_schema_version", "") or ""
            )
            if receipt_schema != item.expected_output_schema:
                raise ValueError("committed tool receipt schema differs from registry")
            return WriteToolOutcome(
                WriteOutcomeStatus.COMMITTED,
                str(result.receipt_id),
                receipt_schema,
                "TOOL_COMMITTED",
            )
        if str(result.effect_status).lower() == "not_committed":
            return WriteToolOutcome(
                WriteOutcomeStatus.NOT_COMMITTED,
                reason_code="TOOL_NOT_COMMITTED",
            )
        return WriteToolOutcome(
            WriteOutcomeStatus.OUTCOME_UNKNOWN,
            reason_code="TOOL_OUTCOME_UNKNOWN",
        )


class _ToolReconciler:
    def __init__(self, tool_manager, context: AgentContextView) -> None:
        self._tools = tool_manager
        self._context = context

    async def reconcile(self, item, *, operation_key):
        reconciliation = item.reconciliation
        if reconciliation is None:
            return WriteToolOutcome(
                WriteOutcomeStatus.OUTCOME_UNKNOWN,
                reason_code="RECONCILIATION_CONTRACT_MISSING",
            )
        arguments = {
            argument.name: argument.value for argument in item.arguments
        }
        try:
            params = {
                name: arguments[name]
                for name in reconciliation.passthrough_arguments
            }
        except KeyError:
            return WriteToolOutcome(
                WriteOutcomeStatus.OUTCOME_UNKNOWN,
                reason_code="RECONCILIATION_ARGUMENT_MISSING",
            )
        params[reconciliation.operation_key_argument] = operation_key
        result = await self._tools.execute_for_agent(
            reconciliation.tool_id,
            params,
            agent_type=_AGENT_TYPE[item.owner_agent],
            context=dict(self._context.trusted_context),
            approved=False,
            call_id=f"reconcile:{operation_key}",
        )
        data = result.data if isinstance(result.data, dict) else {}
        passthrough_matches = all(
            str(data.get(name) or "") == str(arguments[name])
            for name in reconciliation.passthrough_arguments
        )
        receipt_id = str(data.get(reconciliation.receipt_id_field) or "")
        if (
            result.success
            and result.authority == reconciliation.requirement_id
            and str(data.get(reconciliation.operation_key_field) or "") == operation_key
            and passthrough_matches
            and receipt_id
        ):
            return WriteToolOutcome(
                WriteOutcomeStatus.COMMITTED,
                receipt_id,
                item.expected_output_schema,
                "RECONCILED_COMMITTED",
            )
        return WriteToolOutcome(
            WriteOutcomeStatus.OUTCOME_UNKNOWN,
            reason_code="RECONCILIATION_REQUIRED",
        )
