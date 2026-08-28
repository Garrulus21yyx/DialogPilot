import pytest

from services.ticket_service import (
    IdempotencyConflictError,
    InvalidTransitionError,
    TicketNotFoundError,
    TicketPriority,
    TicketService,
    TicketStatus,
)


def create(
    service: TicketService,
    *,
    key="request-1",
    user="user-1",
    question="help",
    published_response="handoff created",
):
    return service.create_ticket(
        idempotency_key=key,
        user_id=user,
        conv_id="conversation-1",
        request_id=key,
        question=question,
        published_response=published_response,
        reason="verification rejected",
        priority=TicketPriority.HIGH,
        agent_type="billing",
        intent="refund",
        verification_status="reject",
    )


def test_ticket_persists_across_service_instances(tmp_path):
    path = tmp_path / "tickets.db"
    first = TicketService(str(path))
    ticket, created = create(first)

    second = TicketService(str(path))
    restored = second.get_ticket(ticket.ticket_id)

    assert created is True
    assert restored == ticket
    assert restored.status is TicketStatus.OPEN
    assert second.get_events(ticket.ticket_id)[0]["to_status"] == "open"


def test_same_idempotent_request_returns_existing_ticket(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    first, first_created = create(service)
    second, second_created = create(service)

    assert first_created is True
    assert second_created is False
    assert second.ticket_id == first.ticket_id
    assert len(service.get_events(first.ticket_id)) == 1


def test_retry_ignores_nondeterministic_generated_wording(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    first, _ = create(service, published_response="first safe handoff wording")
    second, created = create(service, published_response="different retry wording")

    assert created is False
    assert second.ticket_id == first.ticket_id
    assert second.published_response == "first safe handoff wording"


def test_reusing_idempotency_key_with_different_content_conflicts(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    create(service, question="first question")

    with pytest.raises(IdempotencyConflictError):
        create(service, question="different question")


def test_legal_transitions_are_persisted_with_event_history(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    ticket, _ = create(service)

    in_progress = service.transition(
        ticket.ticket_id,
        TicketStatus.IN_PROGRESS,
        actor="agent-7",
        note="investigating",
    )
    resolved = service.transition(
        ticket.ticket_id,
        TicketStatus.RESOLVED,
        actor="agent-7",
        note="customer confirmed",
    )
    closed = service.transition(
        ticket.ticket_id,
        TicketStatus.CLOSED,
        actor="supervisor",
    )

    assert in_progress.status is TicketStatus.IN_PROGRESS
    assert in_progress.assignee == "agent-7"
    assert resolved.status is TicketStatus.RESOLVED
    assert closed.status is TicketStatus.CLOSED
    assert [event["to_status"] for event in service.get_events(ticket.ticket_id)] == [
        "open",
        "in_progress",
        "resolved",
        "closed",
    ]


def test_illegal_transition_and_closed_reopen_fail_deterministically(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    ticket, _ = create(service)

    with pytest.raises(InvalidTransitionError):
        service.transition(ticket.ticket_id, TicketStatus.RESOLVED, actor="agent-1")

    service.transition(ticket.ticket_id, TicketStatus.CLOSED, actor="agent-1")
    with pytest.raises(InvalidTransitionError):
        service.transition(ticket.ticket_id, TicketStatus.IN_PROGRESS, actor="agent-1")


def test_same_status_transition_is_idempotent(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    ticket, _ = create(service)

    unchanged = service.transition(ticket.ticket_id, TicketStatus.OPEN, actor="agent-1")

    assert unchanged.status is TicketStatus.OPEN
    assert len(service.get_events(ticket.ticket_id)) == 1


def test_list_filters_by_user_and_status(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    first, _ = create(service, key="request-1", user="user-1")
    second, _ = create(service, key="request-2", user="user-2")
    service.transition(second.ticket_id, TicketStatus.IN_PROGRESS, actor="agent-2")

    assert [ticket.ticket_id for ticket in service.list_tickets(user_id="user-1")] == [
        first.ticket_id
    ]
    assert [
        ticket.ticket_id
        for ticket in service.list_tickets(status=TicketStatus.IN_PROGRESS)
    ] == [second.ticket_id]


def test_missing_ticket_has_typed_failure(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))

    with pytest.raises(TicketNotFoundError):
        service.get_ticket("missing")
