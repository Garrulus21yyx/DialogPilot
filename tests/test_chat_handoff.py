"""Ticket priority and bounded active-case context at the API boundary."""

import asyncio

from api import main
from core.intent_recognizer import UrgencyLevel
from services.ticket_service import TicketPriority


def test_chat_escalation_creates_one_persistent_idempotent_ticket(ticket_service):
    """The frozen fixture reference now exercises Target's PostgreSQL write owner."""
    from application.agent_result import AgentResultStatus
    from application.conversation_store import ConversationScope
    from application.handoff_runtime import HandoffCommitPolicy, TicketServiceHandoffToolPort
    from application.orchestration_runtime import AgentContextView
    from application.write_workflow import ApprovalGrant, GovernedWriteRuntime
    from infrastructure.postgres_target_runtime import PostgresOperationLedger
    from tests.test_handoff_runtime import _draft, _invocation, _item, NoopReconciler

    invocation, item = _invocation(), _item()
    scope = ConversationScope(invocation.tenant_id, invocation.user_id, invocation.conversation_id)
    accepted = HandoffCommitPolicy().accept(_draft())
    context = AgentContextView(item, "请转人工", (), (), (), 1000)
    first_runtime = GovernedWriteRuntime(
        ledger=PostgresOperationLedger(ticket_service.pool, scope),
        tool_port=TicketServiceHandoffToolPort(ticket_service, invocation, accepted),
        reconciliation_port=NoopReconciler(),
        approval_grants={item.approval_binding: ApprovalGrant(
            item.approval_binding, item.operation_key, item.target_entity_version,
            True, "policy:handoff-commit-policy-v1",
        )},
    )
    first = asyncio.run(first_runtime(context))

    class MustNotCreateAgain:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("committed handoff must replay without ticket creation")

    recovered = GovernedWriteRuntime(
        ledger=PostgresOperationLedger(ticket_service.pool, scope),
        tool_port=MustNotCreateAgain(), reconciliation_port=NoopReconciler(),
        approval_grants={},
    )
    replay = asyncio.run(recovered(context))
    assert first.status is AgentResultStatus.SUCCEEDED
    assert replay.status is AgentResultStatus.SUCCEEDED
    assert replay.action_receipts == first.action_receipts
    tickets = ticket_service.list_tickets()
    assert len(tickets) == 1
    assert tickets[0].ticket_id == first.action_receipts[0].receipt_id


def test_handoff_priority_preserves_typed_critical_urgency():
    """证明 CRITICAL 紧急度能完整投影到人工工单优先级。"""
    assert main._handoff_priority(UrgencyLevel.CRITICAL, "pass") is TicketPriority.CRITICAL
    assert main._handoff_priority(UrgencyLevel.HIGH, "reject") is TicketPriority.HIGH
    assert main._handoff_priority(UrgencyLevel.HIGH, "pass") is TicketPriority.NORMAL



def test_active_ticket_context_is_bounded_authoritative_projection(
    tmp_path, monkeypatch, ticket_service,
):
    service = ticket_service
    ticket, _ = service.create_ticket(
        idempotency_key="request-1",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="request-1",
        question="订单 A123 仍未送达",
        published_response="已经转人工处理",
        reason="delivery overdue",
    )
    monkeypatch.setattr(main, "_ticket_service", service)

    view = asyncio.run(main._active_ticket_context(
        "user-1", query="继续订单问题", intent_or_topics=("other",),
    ))
    section = view.section
    payload = __import__("json").loads(section.content)

    assert section.tag == "active_tickets"
    assert section.priority == 90
    assert payload["authority"] == "TicketService"
    assert payload["cases"][0]["ticket_id"] == ticket.ticket_id
    assert payload["cases"][0]["status"] == "open"
    assert "published_response" not in payload["cases"][0]
