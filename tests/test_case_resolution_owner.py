"""Positive PostgreSQL proof for the authenticated case-resolution owner."""

from application.case_resolution import (
    CASE_RESOLUTION_ACCEPTED,
    AcceptCaseResolution,
)
from application.conversation_projection import (
    ConversationProjectionPolicyV1,
    ConversationSubject,
    ProjectableConversationEvent,
    ProjectionName,
)
from application.conversation_store import (
    ConversationScope,
    TurnRole,
    TurnToAppend,
)
from application.inbound_admission import NewInvocationInbound
from core.auth import Principal
from core.identity import IdentityFactory, TurnId, TurnKey
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_conversation import PostgresConversationTurnStore
from services.ticket_service import TicketPriority, TicketStatus


CREATED = "2026-09-03T10:00:00+00:00"


def test_case_owner_accepts_resolution_with_bound_source_turns(ticket_service):
    service = ticket_service
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-case-owner",
        user_id="user-case-owner",
        conversation_id="conversation-case-owner",
        request_id="request-case-owner",
    )
    PostgresAdmissionUnitOfWork(service.pool).admit_new(
        NewInvocationInbound(
            identity,
            "升级后一直显示 E401，无法登录",
            {"bundle": "case-owner-test-v1"},
            CREATED,
        )
    )
    assistant_turn = TurnKey("case-owner-assistant-turn")
    PostgresConversationTurnStore(service.pool).append_turn(
        ConversationScope(
            identity.tenant_id,
            identity.user_id,
            identity.conversation_id,
        ),
        TurnToAppend(
            turn_key=assistant_turn,
            turn_id=TurnId("case-owner-assistant-turn-id"),
            role=TurnRole.ASSISTANT,
            content="已重置过期令牌并请用户重新登录。",
            created_at="2026-09-03T10:05:00+00:00",
            request_id=identity.request_id,
            invocation_key=identity.invocation_key,
            metadata=identity.metadata(),
        ),
    )
    ticket, _ = service.create_ticket(
        idempotency_key="case-owner-ticket-v1",
        user_id=str(identity.user_id),
        conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id),
        question="升级后 E401 登录失败",
        published_response="已转交支持人员处理",
        reason="需要人工验证修复结果",
        priority=TicketPriority.HIGH,
        agent_type="technical",
        intent="technical_login",
        verification_status="unknown",
        identity_metadata=identity.metadata(),
    )
    active = service.transition(
        ticket.ticket_id,
        TicketStatus.IN_PROGRESS,
        actor="support-agent-1",
        note="accepted case",
    )
    command = AcceptCaseResolution(
        idempotency_key="accept-case-owner-v1",
        expected_ticket_version=active.version,
        resolution="重置过期令牌后恢复登录。",
        authoritative_outcomes=("用户确认已可正常登录",),
    )

    accepted, replayed = service.accept_resolution(
        ticket.ticket_id,
        command,
        principal=Principal("support-agent-1", frozenset({"admin"})),
    )
    replay, replayed_again = service.accept_resolution(
        ticket.ticket_id,
        command,
        principal=Principal("support-agent-1", frozenset({"admin"})),
    )

    assert replayed is False
    assert replayed_again is True
    assert replay == accepted
    assert accepted.subject.user_id == str(identity.user_id)
    assert accepted.case_owner == "support-agent-1"
    assert accepted.source_turn_refs == (
        str(identity.turn_key),
        str(assistant_turn),
    )
    assert service.get_ticket(ticket.ticket_id).status is TicketStatus.RESOLVED
    with service.pool.transaction() as connection:
        facts = connection.execute(
            """
            SELECT event_type,payload
            FROM dialogpilot_app.conversation_events
            WHERE event_type=%s AND payload->>'ticket_id'=%s
        """,
            (CASE_RESOLUTION_ACCEPTED, ticket.ticket_id),
        ).fetchall()
        resolved_events = connection.execute(
            """
            SELECT count(*) FROM dialogpilot_app.handoff_ticket_events
            WHERE ticket_id=%s AND to_status='resolved'
        """,
            (ticket.ticket_id,),
        ).fetchone()[0]
    assert len(facts) == 1
    assert facts[0][1]["source_turn_refs"] == list(accepted.source_turn_refs)
    assert resolved_events == 1

    projection = ProjectableConversationEvent(
        "projection-test",
        ProjectionName.WORKING_WINDOW,
        1,
        accepted.verification_ref,
        1,
        CASE_RESOLUTION_ACCEPTED,
        facts[0][1],
        0,
        ConversationSubject(
            str(identity.tenant_id),
            str(identity.user_id),
            str(identity.conversation_id),
        ),
        1,
    )
    assert ConversationProjectionPolicyV1().allows(projection) is False
