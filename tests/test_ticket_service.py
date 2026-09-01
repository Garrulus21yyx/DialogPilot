import time
import sqlite3

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
    """证明新服务实例仍能读取已经持久化的工单。"""
    path = tmp_path / "tickets.db"
    first = TicketService(str(path))
    ticket, created = create(first)

    second = TicketService(str(path))
    restored = second.get_ticket(ticket.ticket_id)

    assert created is True
    assert restored == ticket
    assert restored.status is TicketStatus.OPEN
    assert second.get_events(ticket.ticket_id)[0]["to_status"] == "open"


def test_ticket_persists_invocation_identity_in_fact_and_outbox(tmp_path):
    service = TicketService(str(tmp_path / "tickets.db"))
    ticket, created = service.create_ticket(
        idempotency_key="operation:v1:ticket",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="request-1",
        question="help",
        published_response="handoff",
        reason="verification rejected",
        identity_metadata={
            "tenant_id": "tenant-1",
            "invocation_key": "invocation:v1:abc",
        },
    )
    assert created is True
    assert ticket.identity_metadata["invocation_key"] == "invocation:v1:abc"
    outbox = service._claim_outbox_message()
    assert outbox is not None
    assert outbox.payload["identity_metadata"] == ticket.identity_metadata


def test_ticket_forward_migrates_legacy_table_before_writing_metadata(tmp_path):
    path = tmp_path / "legacy-tickets.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE tickets (
                ticket_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL, user_id TEXT NOT NULL,
                conv_id TEXT NOT NULL, request_id TEXT NOT NULL,
                question TEXT NOT NULL, published_response TEXT NOT NULL,
                reason TEXT NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL,
                agent_type TEXT NOT NULL, intent TEXT NOT NULL,
                verification_status TEXT NOT NULL, assignee TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
    service = TicketService(str(path))
    ticket, _ = service.create_ticket(
        idempotency_key="operation:v1:migrated",
        user_id="user-1", conv_id="conversation-1", request_id="request-1",
        question="help", published_response="handoff", reason="rejected",
        identity_metadata={"invocation_key": "invocation:v1:x"},
    )
    assert ticket.identity_metadata["invocation_key"] == "invocation:v1:x"


def test_same_idempotent_request_returns_existing_ticket(tmp_path):
    """证明同一幂等操作返回首个工单且 created=False。"""
    service = TicketService(str(tmp_path / "tickets.db"))
    first, first_created = create(service)
    second, second_created = create(service)

    assert first_created is True
    assert second_created is False
    assert second.ticket_id == first.ticket_id
    assert len(service.get_events(first.ticket_id)) == 1


def test_retry_ignores_nondeterministic_generated_wording(tmp_path):
    """证明模型措辞变化不属于客户端操作身份。"""
    service = TicketService(str(tmp_path / "tickets.db"))
    first, _ = create(service, published_response="first safe handoff wording")
    second, created = create(service, published_response="different retry wording")

    assert created is False
    assert second.ticket_id == first.ticket_id
    assert second.published_response == "first safe handoff wording"


def test_reusing_idempotency_key_with_different_content_conflicts(tmp_path):
    """证明同键不同稳定输入会抛出幂等冲突。"""
    service = TicketService(str(tmp_path / "tickets.db"))
    create(service, question="first question")

    with pytest.raises(IdempotencyConflictError):
        create(service, question="different question")


def test_legal_transitions_are_persisted_with_event_history(tmp_path):
    """证明合法迁移与审计事件在持久层保持一致。"""
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
    """证明非法迁移和 CLOSED 重开都按状态机确定性拒绝。"""
    service = TicketService(str(tmp_path / "tickets.db"))
    ticket, _ = create(service)

    with pytest.raises(InvalidTransitionError):
        service.transition(ticket.ticket_id, TicketStatus.RESOLVED, actor="agent-1")

    service.transition(ticket.ticket_id, TicketStatus.CLOSED, actor="agent-1")
    with pytest.raises(InvalidTransitionError):
        service.transition(ticket.ticket_id, TicketStatus.IN_PROGRESS, actor="agent-1")


def test_same_status_transition_is_idempotent(tmp_path):
    """证明重复目标状态不会制造额外事件。"""
    service = TicketService(str(tmp_path / "tickets.db"))
    ticket, _ = create(service)

    unchanged = service.transition(ticket.ticket_id, TicketStatus.OPEN, actor="agent-1")

    assert unchanged.status is TicketStatus.OPEN
    assert len(service.get_events(ticket.ticket_id)) == 1


def test_list_filters_by_user_and_status(tmp_path):
    """证明列表查询同时遵守用户和状态过滤条件。"""
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
    """证明缺失工单使用领域异常，而不是模糊空值。"""
    service = TicketService(str(tmp_path / "tickets.db"))

    with pytest.raises(TicketNotFoundError):
        service.get_ticket("missing")


def test_active_ticket_projection_excludes_only_closed_terminal_state(tmp_path):
    """未关闭事项包含可恢复状态，CLOSED 终态不会继续污染客服上下文。"""
    service = TicketService(str(tmp_path / "tickets.db"))
    open_ticket, _ = create(service, key="open", user="user-1")
    waiting, _ = create(service, key="waiting", user="user-1")
    closed, _ = create(service, key="closed", user="user-1")
    service.transition(waiting.ticket_id, TicketStatus.IN_PROGRESS, actor="agent-1")
    service.transition(waiting.ticket_id, TicketStatus.WAITING_CUSTOMER, actor="agent-1")
    service.transition(closed.ticket_id, TicketStatus.CLOSED, actor="agent-1")
    create(service, key="other-user", user="user-2")

    active = service.list_active_tickets(user_id="user-1", limit=3)

    assert {ticket.ticket_id for ticket in active} == {open_ticket.ticket_id, waiting.ticket_id}
    assert all(ticket.status is not TicketStatus.CLOSED for ticket in active)


def test_ticket_and_outbox_event_commit_in_same_transaction(tmp_path):
    """outbox 写失败必须回滚工单，不能留下无法可靠投递的半成功。"""
    service = TicketService(str(tmp_path / "tickets.db"))
    with service._connect() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_ticket_outbox BEFORE INSERT ON ticket_outbox
            BEGIN SELECT RAISE(ABORT, 'outbox unavailable'); END
            """
        )

    with pytest.raises(Exception, match="outbox unavailable"):
        create(service)

    assert service.list_tickets() == []
    assert service.outbox_stats()["pending"] == 0


def test_idempotent_ticket_creates_one_outbox_event_and_dispatches_once(tmp_path):
    delivered = []
    service = TicketService(
        str(tmp_path / "tickets.db"), dispatcher=delivered.append,
    )
    first, first_created = create(service)
    second, second_created = create(service)

    assert first_created is True
    assert second_created is False
    assert second.ticket_id == first.ticket_id
    assert service.outbox_stats()["pending"] == 1
    assert service.dispatch_once() is True
    assert service.dispatch_once() is False
    assert len(delivered) == 1
    assert delivered[0].ticket_id == first.ticket_id
    assert delivered[0].payload["idempotency_key"] == "request-1"
    assert service.outbox_stats()["delivered"] == 1


def test_failed_dispatch_remains_pending_for_retry_without_fake_success(tmp_path):
    def fail(_message):
        raise RuntimeError("crm unavailable")

    service = TicketService(
        str(tmp_path / "tickets.db"),
        dispatcher=fail,
        dispatch_retry_base_seconds=60,
    )
    create(service)

    assert service.dispatch_once() is True
    stats = service.outbox_stats()
    assert stats["pending"] == 1
    assert stats["delivered"] == 0
    assert stats["due"] == 0


def test_failed_outbox_event_is_recovered_by_restarted_worker(tmp_path):
    path = tmp_path / "tickets.db"

    def fail(_message):
        raise RuntimeError("first worker crashed")

    first = TicketService(
        str(path), dispatcher=fail, dispatch_retry_base_seconds=0.05,
    )
    create(first)
    assert first.dispatch_once() is True

    delivered = []
    restarted = TicketService(str(path), dispatcher=delivered.append)
    time.sleep(0.07)
    assert restarted.dispatch_once() is True
    assert len(delivered) == 1
    assert delivered[0].attempts == 2
    assert restarted.outbox_stats()["delivered"] == 1
"""TicketService 持久化、幂等身份和闭合状态机测试。"""
