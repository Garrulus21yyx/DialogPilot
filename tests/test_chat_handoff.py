"""Ticket priority and bounded active-case context at the API boundary."""

import asyncio

from api import main
from core.intent_recognizer import UrgencyLevel
from services.ticket_service import TicketPriority


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
