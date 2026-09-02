"""PostgreSQL is the single runtime owner of Handoff tickets."""
import pytest

from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_ticket_service import PostgresTicketService
from services.ticket_service import (
    IdempotencyConflictError,
    InvalidTransitionError,
    TicketPriority,
    TicketStatus,
)


@pytest.fixture()
def tickets(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("TRUNCATE dialogpilot_app.handoff_tickets CASCADE")
    try:
        yield PostgresTicketService(pool), pool
    finally:
        pool.close()


def _create(service, *, key="ticket-key", question="I need help"):
    return service.create_ticket(
        idempotency_key=key, user_id="user-a", conv_id="conv-a",
        request_id="request-a", question=question,
        published_response="safe handoff", reason="verification unknown",
        priority=TicketPriority.HIGH, agent_type="technical",
        intent="technical_crash", verification_status="unknown",
        identity_metadata={"invocation_key": "invocation-a"},
    )


def test_create_replay_transition_and_events_are_one_transactional_history(tickets):
    service, _pool = tickets
    first, created = _create(service)
    replay, replay_created = _create(service)

    assert created is True
    assert replay_created is False
    assert replay.ticket_id == first.ticket_id
    assert service.outbox_stats()["pending"] == 1

    active = service.transition(
        first.ticket_id, TicketStatus.IN_PROGRESS,
        actor="agent-one", note="claimed",
    )
    resolved = service.transition(
        first.ticket_id, TicketStatus.RESOLVED,
        actor="agent-one", note="resolved after contact",
    )
    assert active.assignee == "agent-one"
    assert resolved.version == 3
    assert [item["to_status"] for item in service.get_events(first.ticket_id)] == [
        "open", "in_progress", "resolved",
    ]
    assert service.list_active_tickets(user_id="user-a") == [resolved]


def test_idempotency_and_state_algebra_fail_closed(tickets):
    service, _pool = tickets
    ticket, _ = _create(service)
    with pytest.raises(IdempotencyConflictError):
        _create(service, question="different request")
    with pytest.raises(InvalidTransitionError):
        service.transition(
            ticket.ticket_id, TicketStatus.RESOLVED, actor="agent-one",
        )


def test_outbox_claim_delivery_is_persistent(tickets):
    _service, pool = tickets
    delivered = []
    service = PostgresTicketService(pool, dispatcher=delivered.append)
    ticket, _ = _create(service)

    assert service.dispatch_once() is True
    assert delivered[0].ticket_id == ticket.ticket_id
    assert service.outbox_stats() == {
        "dispatcher_configured": True,
        "worker_running": False,
        "pending": 0,
        "delivered": 1,
        "due": 0,
    }
