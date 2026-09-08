"""Target v1 workflow executor for governed ToolManager business writes."""
from __future__ import annotations

import json

from application.agent_result import AgentResult, AgentResultStatus
from application.capability_registry import ApprovalPolicy
from application.conversation_store import ConversationScope
from application.handoff_runtime import (
    AcceptedHandoff,
    HandoffCommitPolicy,
    HandoffDraft,
)
from application.orchestration_runtime import AgentContextView
from application.write_workflow import (
    ApprovalGrant,
    GovernedWriteRuntime,
    WriteOutcomeStatus,
    WriteToolOutcome,
)
from application.work_control import WorkControlGuard
from core.identity import ConversationId, TenantId, UserId
from infrastructure.postgres_target_runtime import PostgresOperationLedger
from infrastructure.postgres_write_recovery import PostgresWriteRecoveryReview
from infrastructure.target_agent_result_adapter import fact_from_tool_result


class TargetWorkflowExecutor:
    """Bind each write to its conversation ledger and registry approval mode."""

    version = "target-workflow-executor-v1"

    def __init__(
        self, pool, tool_manager, *, registry, control_guard: WorkControlGuard | None = None,
    ) -> None:
        self._pool = pool
        self._tools = tool_manager
        self._control_guard = control_guard
        self._registry = registry

    async def __call__(self, context: AgentContextView) -> AgentResult:
        item = context.work_item
        trusted = context.trusted_context
        scope = ConversationScope(
            TenantId(trusted["tenant_id"]),
            UserId(trusted["user_id"]),
            ConversationId(trusted["conversation_id"]),
        )
        grants = self._approval_grants(item, trusted)
        accepted_handoff = self._accepted_handoff(context)
        runtime = GovernedWriteRuntime(
            ledger=PostgresOperationLedger(self._pool, scope),
            tool_port=_ToolPort(
                self._tools,
                context,
                accepted_handoff=accepted_handoff,
                control_guard=self._control_guard,
                principal=self._registry.agent(item.owner_agent).execution_principal,
            ),
            reconciliation_port=_ToolReconciler(self._tools, context,
                principal=self._registry.agent(item.owner_agent).execution_principal),
            approval_grants=grants,
            manual_review=PostgresWriteRecoveryReview(self._pool, scope),
        )
        return await runtime(context)

    @staticmethod
    def _accepted_handoff(
        context: AgentContextView,
    ) -> AcceptedHandoff | None:
        item = context.work_item
        if "support.handoff_action" not in item.requirement_ids:
            return None
        arguments = {
            argument.name: argument.value for argument in item.arguments
        }
        reason = str(arguments.get("reason") or "USER_REQUEST").strip()
        summary = str(arguments.get("summary") or item.objective).strip()
        draft = HandoffDraft(
            handoff_id=str(item.operation_key),
            target_queue="customer_support",
            user_goal=context.current_message or item.objective,
            problem_summary=summary,
            reason_codes=(reason,),
            verified_facts=context.verified_facts,
            user_assertions=(context.current_message,)
            if context.current_message else (),
            action_receipts=tuple(
                receipt
                for result in context.dependency_results
                for receipt in result.action_receipts
            ),
            missing_materials=tuple(
                field.field_name
                for result in context.dependency_results
                for field in result.missing_inputs
            ),
            media_evidence_refs=context.evidence_refs,
            risk=item.risk.value,
            commitments_and_sla=(),
            recommended_next_action=(
                "Review the verified context and continue the customer case."
            ),
            explicit_user_request=(
                item.approval_policy is ApprovalPolicy.USER_COMMAND_SUFFICIENT
            ),
            actions_attempted=tuple(
                f"{result.owner_agent}:{result.reason_code}"
                for result in context.dependency_results
            ),
        )
        return HandoffCommitPolicy().accept(draft)

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
    def __init__(
        self,
        tool_manager,
        context: AgentContextView,
        *,
        principal: str,
        accepted_handoff: AcceptedHandoff | None = None,
        control_guard: WorkControlGuard | None = None,
    ) -> None:
        self._tools = tool_manager
        self._context = context
        self._accepted_handoff = accepted_handoff
        self._principal = principal
        self._control_guard = control_guard

    async def execute(self, item, *, tool_id, arguments, operation_key):
        if self._control_guard is not None:
            self._control_guard.ensure_current(
                self._context.work_item, self._context.trusted_context,
            )
        context = {
            **dict(self._context.trusted_context),
            "business_operation_key": operation_key,
        }
        if tool_id == "support_ticket_create":
            if self._accepted_handoff is None:
                raise ValueError("handoff write requires an accepted draft")
            context["handoff_contract_json"] = json.dumps(
                {
                    "policy_version": self._accepted_handoff.policy_version,
                    "policy_reason_code": self._accepted_handoff.reason_code,
                    "draft": self._accepted_handoff.draft.to_payload(),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        result = await self._tools.execute_for_agent(
            tool_id,
            dict(arguments),
            agent_type=self._principal,
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
                facts=(fact_from_tool_result(item, result),),
            )
        if str(result.effect_status).lower() == "not_committed":
            return WriteToolOutcome(
                (WriteOutcomeStatus.REJECTED if str(result.status).lower() == "rejected"
                 else WriteOutcomeStatus.NOT_COMMITTED),
                reason_code=(str((result.data or {}).get("reason_code", "TOOL_REJECTED"))
                    if str(result.status).lower() == "rejected" and isinstance(result.data, dict)
                    else "TOOL_NOT_COMMITTED"),
                detail=str(result.error or "") if str(result.status).lower() == "rejected" else "",
                source_ref=str(result.call_id or operation_key),
            )
        return WriteToolOutcome(
            WriteOutcomeStatus.OUTCOME_UNKNOWN,
            reason_code="TOOL_OUTCOME_UNKNOWN",
        )


class _ToolReconciler:
    def __init__(self, tool_manager, context: AgentContextView, *, principal: str) -> None:
        self._tools = tool_manager
        self._context = context
        self._principal = principal

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
            agent_type=self._principal,
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
                facts=(fact_from_tool_result(item, result),),
            )
        return WriteToolOutcome(
            WriteOutcomeStatus.OUTCOME_UNKNOWN,
            reason_code="RECONCILIATION_REQUIRED",
        )
