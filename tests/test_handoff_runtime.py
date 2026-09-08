import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from application.agent_result import AgentResult, AgentResultStatus, ReceiptRef
from application.capability_registry import (
    ActionReconciliationDefinition,
    ApprovalPolicy,
    CapabilityEffect,
    CapabilityRisk,
)
from application.conversation_state import ConversationOwner, ConversationState, ConversationStateError
from application.delivery_contract import ConnectorCapability
from application.handoff_runtime import (
    HandoffCommitPolicy,
    HandoffContractError,
    HandoffDraft,
    HandoffPublicationSelector,
    TicketServiceHandoffToolPort,
)
from application.orchestration_runtime import AgentContextView
from application.publication import ProjectionDisposition, PublicationPolicy
from application.work_item import ControlMode, WorkItem
from application.write_workflow import (
    ApprovalGrant,
    GovernedWriteRuntime,
    InMemoryOperationLedger,
    WriteOutcomeStatus,
    WriteToolOutcome,
)
from core.identity import IdentityFactory
from infrastructure.target_workflow_execution import (
    TargetWorkflowExecutor,
    _ToolPort,
    _ToolReconciler,
)


def _invocation():
    return IdentityFactory(lambda: "fixed").create_invocation(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conversation-a",
        request_id="request-a",
    )


def _draft(**changes):
    values = {
        "handoff_id": "handoff-1",
        "target_queue": "refund-specialist",
        "user_goal": "Resolve a disputed refund",
        "problem_summary": "Refund status conflicts with the prior receipt",
        "reason_codes": ("EVIDENCE_CONFLICT",),
        "verified_facts": (),
        "user_assertions": ("Customer says funds did not arrive",),
        "action_receipts": (),
        "missing_materials": (),
        "media_evidence_refs": (),
        "risk": "HIGH",
        "commitments_and_sla": (),
        "recommended_next_action": "Review payment processor receipt",
    }
    values.update(changes)
    return HandoffDraft(**values)


def _item():
    return WorkItem(
        "handoff-write-1",
        "human_service",
        "Create human support ticket",
        ControlMode.WORKFLOW,
        ("support_ticket_create", "support_ticket_by_operation"),
        (),
        (),
        ("support.handoff_action",),
        (),
        CapabilityEffect.WRITE,
        CapabilityRisk.HIGH,
        "ticket-receipt-v1",
        "handoff:v1",
        2,
        "registry:v1:test",
        5,
        2,
        flow_ref="human_handoff:v1",
        operation_key="handoff-operation-1",
        approval_binding="handoff-approval-1",
        target_entity_version="conversation-a:v2",
        reconciliation=ActionReconciliationDefinition(
            "support_ticket_by_operation", "support.ticket_state",
            "operation_key", "operation_key", (), "ticket_id",
        ),
        aggregate_ref="conversation:conversation-a",
        action_ref="support.handoff.create:v1",
        approval_policy=ApprovalPolicy.USER_COMMAND_SUFFICIENT,
    )


class TicketService:
    def __init__(self):
        self.calls = []

    def create_ticket(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(ticket_id="ticket-1"), True


class NoopReconciler:
    async def reconcile(self, item, *, operation_key):
        return WriteToolOutcome(
            WriteOutcomeStatus.OUTCOME_UNKNOWN,
            reason_code="TICKET_STATUS_UNKNOWN",
        )


def _publication_policy():
    return PublicationPolicy(
        ConnectorCapability.IDEMPOTENT_SEND,
        3,
        "delivery-retry-v1",
        "2026-09-04T00:00:00+00:00",
    )


def test_user_command_approval_is_selected_by_policy_not_flow_name():
    item = replace(_item(), flow_ref="renamed-handoff-flow:v9")

    grants = TargetWorkflowExecutor._approval_grants(item, {
        "request_id": "request-a",
        "user_id": "user-a",
    })

    grant = grants[item.approval_binding]
    assert grant.operation_key == item.operation_key
    assert grant.actor_ref == "user-command:request-a"


def test_unknown_handoff_outcome_uses_registry_reconciliation_not_flow_name():
    item = replace(_item(), flow_ref="renamed-handoff-flow:v9")

    class Tools:
        def __init__(self):
            self.calls = []

        async def execute_for_agent(self, name, params, **kwargs):
            self.calls.append((name, params, kwargs))
            from mcp.tool_manager import ToolResult
            return ToolResult(
                success=True,
                tool_name=name,
                call_id="status-call-1",
                authority="support.ticket_state",
                data={
                    "ticket_id": "ticket-1",
                    "operation_key": item.operation_key,
                },
            )

    tools = Tools()
    context = AgentContextView(
        item, "human please", (), (), (), 1000,
        {"tenant_id": "tenant-a", "user_id": "user-a", "conversation_id": "conversation-a"},
    )

    outcome = asyncio.run(_ToolReconciler(tools, context, principal="escalation").reconcile(
        item, operation_key=item.operation_key,
    ))

    assert outcome.status is WriteOutcomeStatus.COMMITTED
    assert outcome.receipt_id == "ticket-1"
    assert tools.calls[0][0] == "support_ticket_by_operation"


def test_target_handoff_write_requires_policy_accepted_draft_and_forwards_it():
    item = _item()
    upstream = AgentResult(
        "refund-read",
        "billing_refund",
        AgentResultStatus.SUCCEEDED,
        "REFUND_STATUS_FOUND",
        "refund-agent-v1",
        action_receipts=(ReceiptRef(
            "refund-receipt-1",
            "action-receipt-v1",
            "refund-operation-1",
            "COMMITTED",
            "refund.request_action",
        ),),
    )
    context = AgentContextView(
        item,
        "Please transfer this case to a person",
        (),
        (),
        ("evidence:conversation",),
        1000,
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "conversation_id": "conversation-a",
            "request_id": "request-a",
        },
        (upstream,),
    )
    accepted = TargetWorkflowExecutor._accepted_handoff(context)

    class Tools:
        def __init__(self):
            self.context = None

        async def execute_for_agent(self, _name, _params, **kwargs):
            self.context = kwargs["context"]
            from mcp.tool_manager import ToolResult
            return ToolResult(
                success=True,
                tool_name=_name,
                data={"ticket_id": "ticket-1"},
                authority="support.handoff_action",
                effect_status="committed",
                receipt_schema_version="ticket-receipt-v1",
                receipt_id="ticket-1",
            )

    tools = Tools()
    outcome = asyncio.run(_ToolPort(
        tools,
        context,
        principal="escalation",
        accepted_handoff=accepted,
    ).execute(
        item,
        tool_id="support_ticket_create",
        arguments={},
        operation_key=item.operation_key,
    ))

    assert accepted is not None
    assert accepted.reason_code == "EXPLICIT_USER_HANDOFF"
    assert outcome.status is WriteOutcomeStatus.COMMITTED
    contract = json.loads(tools.context["handoff_contract_json"])
    assert contract["draft"]["handoff_id"] == "handoff-operation-1"
    assert contract["draft"]["actions_attempted"] == [
        "billing_refund:REFUND_STATUS_FOUND",
    ]
    assert contract["draft"]["action_receipts"][0]["receipt_id"] == (
        "refund-receipt-1"
    )

    with pytest.raises(ValueError, match="accepted draft"):
        asyncio.run(_ToolPort(tools, context, principal="escalation").execute(
            item,
            tool_id="support_ticket_create",
            arguments={},
            operation_key=item.operation_key,
        ))


def test_handoff_commit_receipt_transfers_owner_and_allows_success_claim():
    invocation = _invocation()
    accepted = HandoffCommitPolicy().accept(_draft())
    tickets = TicketService()
    runtime = GovernedWriteRuntime(
        ledger=InMemoryOperationLedger(),
        tool_port=TicketServiceHandoffToolPort(tickets, invocation, accepted),
        reconciliation_port=NoopReconciler(),
        approval_grants={"handoff-approval-1": ApprovalGrant(
            "handoff-approval-1",
            "handoff-operation-1",
            "conversation-a:v2",
            True,
            "policy:handoff-commit-policy-v1",
        )},
    )

    result = asyncio.run(runtime(AgentContextView(_item(), "human please", (), (), (), 1000)))

    assert result.status is AgentResultStatus.SUCCEEDED
    replay = asyncio.run(runtime(AgentContextView(_item(), "human please", (), (), (), 1000)))
    assert replay.status is AgentResultStatus.SUCCEEDED
    assert replay.action_receipts == result.action_receipts
    assert len(tickets.calls) == 1
    assert tickets.calls[0]["idempotency_key"] == "handoff-operation-1"
    receipt = result.action_receipts[0]
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    ).transfer_to_human(receipt)
    assert state.owner is ConversationOwner.HUMAN
    assert state.human_ticket_ref == "ticket-1"

    publication = HandoffPublicationSelector().select(
        invocation=invocation,
        state=state,
        result=result,
        bundle_version="customer-service-v1",
        created_at="2026-09-03T12:00:00+00:00",
        policy=_publication_policy(),
    )
    assert "ticket-1" in publication.response_text
    assert publication.verifier_status == "PASS"


def test_draft_or_failed_result_cannot_claim_ticket_was_created():
    invocation = _invocation()
    result = AgentResult(
        "handoff-write-1",
        "human_service",
        AgentResultStatus.RECONCILING,
        "TICKET_OUTCOME_UNKNOWN",
        "handoff-runtime-v1",
    )

    publication = HandoffPublicationSelector().select(
        invocation=invocation,
        state=ConversationState.empty(tenant_id=str(invocation.tenant_id),
            user_id=str(invocation.user_id), conversation_id=str(invocation.conversation_id)),
        result=result,
        bundle_version="customer-service-v1",
        created_at="2026-09-03T12:00:00+00:00",
        policy=_publication_policy(),
    )

    assert "已创建" not in publication.response_text
    assert "尚未确认" in publication.response_text
    assert publication.projection_disposition is ProjectionDisposition.CLARIFICATION


def test_conversation_rejects_non_handoff_or_uncommitted_receipt():
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )
    wrong = ReceiptRef(
        "refund-1", "receipt-v1", "operation-1", "COMMITTED",
        "refund.request_action",
    )
    with pytest.raises(ConversationStateError, match="does not prove"):
        state.transfer_to_human(wrong)

    unknown = ReceiptRef(
        "ticket-1", "receipt-v1", "operation-1", "OUTCOME_UNKNOWN",
        "support.handoff_action",
    )
    with pytest.raises(ConversationStateError, match="not committed"):
        state.transfer_to_human(unknown)


def test_handoff_policy_requires_user_request_or_registered_system_reason():
    with pytest.raises(HandoffContractError, match="no registered commit reason"):
        HandoffCommitPolicy().accept(_draft(reason_codes=("MODEL_UNCERTAIN",)))

    accepted = HandoffCommitPolicy().accept(_draft(
        reason_codes=("USER_REQUEST",), explicit_user_request=True,
    ))
    assert accepted.reason_code == "EXPLICIT_USER_HANDOFF"


def test_human_reply_requires_ticket_owned_conversation():
    selector = HandoffPublicationSelector()
    automated = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )
    with pytest.raises(HandoffContractError, match="automation-owned"):
        selector.select_human_reply(
            state=automated,
            ticket_id="ticket-1",
            handoff_id="handoff-1",
            human_message_id="human-message-1",
            text="I have reviewed your case.",
            created_at="2026-09-03T12:00:00+00:00",
            policy=_publication_policy(),
        )

    receipt = ReceiptRef(
        "ticket-1", "ticket-receipt-v1", "operation-1", "COMMITTED",
        "support.handoff_action",
    )
    human_owned = automated.transfer_to_human(receipt)
    command = selector.select_human_reply(
        state=human_owned,
        ticket_id="ticket-1",
        handoff_id="handoff-1",
        human_message_id="human-message-1",
        text="I have reviewed your case.",
        created_at="2026-09-03T12:00:00+00:00",
        policy=_publication_policy(),
    )

    assert command.ticket_id == "ticket-1"
    assert command.text == "I have reviewed your case."
