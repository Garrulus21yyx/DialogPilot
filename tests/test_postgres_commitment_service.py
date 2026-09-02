from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_commitment_service import PostgresCommitmentService
from services.commitment_service import (
    CommitmentIdempotencyConflictError,
    CommitmentStatus,
    CommitmentVersionConflictError,
    InvalidCommitmentTransitionError,
)


@pytest.fixture()
def commitments(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("TRUNCATE dialogpilot_app.commitments CASCADE")
    try:
        yield PostgresCommitmentService(pool)
    finally:
        pool.close()


def _create(service, *, key="promise-1", due=None, source_kind="manual", receipt=None):
    return service.create(
        idempotency_key=key,
        user_id="user-a",
        conversation_id="conv-a",
        kind="refund_followup",
        description="明天 18:00 前确认退款状态",
        due_at=due or datetime(2030, 1, 1, tzinfo=timezone.utc),
        owner="support-agent-a",
        source_kind=source_kind,
        source_receipt_ref=receipt,
    )


def test_explicit_create_is_idempotent_and_conflicts_on_changed_fact(commitments):
    first, created = _create(commitments)
    replay, replay_created = _create(commitments)

    assert created is True
    assert replay_created is False
    assert replay == first
    assert commitments.events(first.commitment_id)[0]["to_status"] == "scheduled"

    with pytest.raises(CommitmentIdempotencyConflictError):
        commitments.create(
            idempotency_key="promise-1", user_id="user-a", conversation_id="conv-a",
            kind="different", description="changed",
            due_at=datetime.now(timezone.utc) + timedelta(hours=2),
            owner="support-agent-a", source_kind="manual",
        )


def test_business_action_requires_authoritative_receipt(commitments):
    with pytest.raises(ValueError, match="requires source_receipt_ref"):
        _create(commitments, source_kind="business_action")

    item, _ = _create(
        commitments, source_kind="business_action", receipt="tool-receipt:refund:1",
    )
    assert item.source_receipt_ref == "tool-receipt:refund:1"


def test_due_then_receipt_becomes_late_fulfilled_and_keeps_breach_time(commitments):
    item, _ = _create(
        commitments, due=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    [breached] = commitments.breach_due()
    fulfilled = commitments.transition(
        item.commitment_id,
        CommitmentStatus.LATE_FULFILLED,
        expected_version=breached.version,
        actor="refund-worker",
        receipt_ref="refund-status:confirmed:1",
    )

    assert breached.status is CommitmentStatus.BREACHED
    assert fulfilled.status is CommitmentStatus.LATE_FULFILLED
    assert fulfilled.breached_at is not None
    assert fulfilled.fulfilled_at is not None
    assert [event["version"] for event in commitments.events(item.commitment_id)] == [1, 2, 3]


def test_fulfillment_first_cannot_be_relabelled_breached(commitments):
    item, _ = _create(commitments)
    fulfilled = commitments.transition(
        item.commitment_id,
        CommitmentStatus.FULFILLED,
        expected_version=1,
        actor="business-worker",
        receipt_ref="business-receipt:1",
    )
    assert commitments.breach_due(
        now=datetime.now(timezone.utc) + timedelta(days=1),
    ) == []
    with pytest.raises(InvalidCommitmentTransitionError):
        commitments.transition(
            item.commitment_id, CommitmentStatus.BREACHED,
            expected_version=fulfilled.version, actor="due-worker",
        )


def test_version_cas_and_fulfillment_receipt_fail_closed(commitments):
    item, _ = _create(commitments)
    with pytest.raises(CommitmentVersionConflictError):
        commitments.transition(
            item.commitment_id, CommitmentStatus.CANCELLED,
            expected_version=99, actor="support-agent-a",
        )
    with pytest.raises(ValueError, match="requires receipt_ref"):
        commitments.transition(
            item.commitment_id, CommitmentStatus.FULFILLED,
            expected_version=1, actor="support-agent-a",
        )


def test_breached_refs_are_typed_and_only_include_open_risk(commitments):
    item, _ = _create(
        commitments, due=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    commitments.breach_due()
    assert commitments.breached_refs(user_id="user-a") == (
        f"breached:commitment:{item.commitment_id}:v2",
    )
    commitments.transition(
        item.commitment_id, CommitmentStatus.LATE_FULFILLED,
        expected_version=2, actor="worker", receipt_ref="receipt:late:1",
    )
    assert commitments.breached_refs(user_id="user-a") == ()
