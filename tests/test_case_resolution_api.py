"""Positive HTTP proof for authenticated case-owner resolution acceptance."""

from fastapi.testclient import TestClient

from api import main
from application.case_resolution import CASE_RESOLUTION_ACCEPTED
from application.inbound_admission import NewInvocationInbound
from core.auth import Principal
from core.identity import IdentityFactory
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from services.ticket_service import TicketPriority, TicketStatus


def test_authenticated_case_owner_accepts_resolution_over_http(
    ticket_service,
    monkeypatch,
):
    service = ticket_service
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-resolution-api",
        user_id="user-resolution-api",
        conversation_id="conversation-resolution-api",
        request_id="request-resolution-api",
    )
    PostgresAdmissionUnitOfWork(service.pool).admit_new(
        NewInvocationInbound(
            identity,
            "升级后 E401 无法登录",
            {"bundle": "case-resolution-api-v1"},
            "2026-09-03T12:00:00+00:00",
        )
    )
    ticket, _ = service.create_ticket(
        idempotency_key="resolution-api-ticket-v1",
        user_id=str(identity.user_id),
        conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id),
        question="升级后 E401 无法登录",
        published_response="已转交支持人员处理",
        reason="需要人工修复并验收",
        priority=TicketPriority.HIGH,
        agent_type="technical",
        intent="technical_login",
        verification_status="unknown",
        identity_metadata=identity.metadata(),
    )
    active = service.transition(
        ticket.ticket_id,
        TicketStatus.IN_PROGRESS,
        actor="support-resolution-owner",
    )
    monkeypatch.setattr(main, "_ticket_service", service)
    main.app.dependency_overrides[main.get_principal] = lambda: Principal(
        "support-resolution-owner",
        frozenset({"admin"}),
    )
    body = {
        "idempotency_key": "resolution-api-accept-v1",
        "expected_ticket_version": active.version,
        "resolution": "重置过期令牌后恢复登录。",
        "authoritative_outcomes": ["用户确认 E401 已消失且登录成功"],
    }

    client = TestClient(main.app)
    try:
        response = client.post(
            f"/tickets/{ticket.ticket_id}/resolution",
            json=body,
        )
        replay = client.post(
            f"/tickets/{ticket.ticket_id}/resolution",
            json=body,
        )
    finally:
        client.close()
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["replayed"] is False
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    accepted = response.json()["accepted_resolution"]
    assert accepted["case_owner"] == "support-resolution-owner"
    assert accepted["source_invocation_key"] == str(identity.invocation_key)
    assert service.get_ticket(ticket.ticket_id).status is TicketStatus.RESOLVED
    with service.pool.transaction() as connection:
        fact_count = connection.execute(
            """
            SELECT count(*) FROM dialogpilot_app.conversation_events
            WHERE event_type=%s AND payload->>'ticket_id'=%s
        """,
            (CASE_RESOLUTION_ACCEPTED, ticket.ticket_id),
        ).fetchone()[0]
    assert fact_count == 1
