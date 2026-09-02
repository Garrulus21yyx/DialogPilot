"""M1-T01 PostgreSQL immutable transcript and admission repository tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg
import pytest

from application.admission_contract import (
    AdmissionConflict,
    AdmissionCreated,
    AdmissionExisting,
    AdmissionRecord,
    AdmissionStatus,
    CasAlreadyApplied,
    CasApplied,
    ExecutionPointer,
    request_fingerprint,
)
from application.conversation_store import (
    AppendStatus,
    ConversationAccessDenied,
    ConversationScope,
    EventToAppend,
    TurnRole,
    TurnToAppend,
)
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_conversation import (
    InvocationToCreate,
    PostgresConversationTurnStore,
    PostgresInvocationRepository,
)


@pytest.fixture()
def conversation_repositories(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=8,
    ))
    pool.open()
    try:
        yield (
            pool,
            PostgresConversationTurnStore(pool),
            PostgresInvocationRepository(pool),
        )
    finally:
        pool.close()


def _identity(suffix="1", tenant="tenant-a", user="user-a", conversation="conv-a"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id=tenant,
        user_id=user,
        conversation_id=conversation,
        request_id=f"request-{suffix}",
    )


def _scope(identity):
    return ConversationScope(
        identity.tenant_id, identity.user_id, identity.conversation_id,
    )


def _turn(identity, content="hello"):
    return TurnToAppend(
        turn_key=identity.turn_key,
        turn_id=identity.turn_id,
        role=TurnRole.INBOUND,
        content=content,
        created_at="2026-09-02T03:00:00+02:00",
        request_id=identity.request_id,
        invocation_key=identity.invocation_key,
        metadata={"source": "test"},
    )


def test_same_turn_key_is_applied_once_and_content_conflict_is_typed(
    conversation_repositories,
):
    _, turns, _ = conversation_repositories
    identity = _identity()
    first = turns.append_turn(_scope(identity), _turn(identity))
    replay = turns.append_turn(_scope(identity), _turn(identity))
    conflict = turns.append_turn(_scope(identity), _turn(identity, "changed"))

    assert first.status is AppendStatus.APPLIED
    assert replay.status is AppendStatus.ALREADY_APPLIED
    assert conflict.status is AppendStatus.IDEMPOTENCY_CONFLICT
    assert [item.seq for item in turns.list_turns(_scope(identity))] == [1]


def test_concurrent_same_turn_key_has_one_row_and_no_sequence_hole(
    conversation_repositories,
):
    _, turns, _ = conversation_repositories
    identity = _identity("concurrent", conversation="conv-concurrent")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _: turns.append_turn(_scope(identity), _turn(identity)), range(16),
        ))
    assert sum(item.status is AppendStatus.APPLIED for item in results) == 1
    assert sum(item.status is AppendStatus.ALREADY_APPLIED for item in results) == 15
    assert [item.seq for item in turns.list_turns(_scope(identity))] == [1]


def test_failed_turn_transaction_rolls_back_sequence_allocation(
    conversation_repositories,
):
    _, turns, _ = conversation_repositories
    first_identity = _identity("gap-1", conversation="conv-gap")
    second_identity = _identity("gap-2", conversation="conv-gap")
    third_identity = _identity("gap-3", conversation="conv-gap")
    turns.append_turn(_scope(first_identity), _turn(first_identity, "first"))
    duplicate_turn_id = TurnToAppend(
        **{**_turn(second_identity, "must rollback").__dict__,
           "turn_id": first_identity.turn_id},
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        turns.append_turn(_scope(second_identity), duplicate_turn_id)
    turns.append_turn(_scope(third_identity), _turn(third_identity, "third"))
    assert [item.seq for item in turns.list_turns(_scope(first_identity))] == [1, 2]


def test_turn_and_event_sequences_are_independent_immutable_facts(
    conversation_repositories,
):
    pool, turns, _ = conversation_repositories
    identity = _identity("event", conversation="conv-event")
    turns.append_turn(_scope(identity), _turn(identity))
    operation = identity.operation_key("Conversation", "append_event", "accepted")
    event = EventToAppend(
        event_id="event-1", operation_key=operation,
        event_type="REQUEST_ACCEPTED", payload={"request_id": str(identity.request_id)},
        created_at="2026-09-02T03:00:01+02:00",
        request_id=identity.request_id, invocation_key=identity.invocation_key,
    )
    assert turns.append_event(_scope(identity), event) is AppendStatus.APPLIED
    assert turns.append_event(_scope(identity), event) is AppendStatus.ALREADY_APPLIED
    changed = EventToAppend(
        **{**event.__dict__, "payload": {"request_id": "changed"}},
    )
    assert turns.append_event(_scope(identity), changed) is AppendStatus.IDEMPOTENCY_CONFLICT

    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable"):
        with pool.transaction() as connection:
            connection.execute(
                "UPDATE dialogpilot_app.conversation_turns SET content='mutated' "
                "WHERE turn_key=%s", (str(identity.turn_key),),
            )


def test_cross_tenant_or_user_transcript_and_invocation_reads_are_rejected(
    conversation_repositories,
):
    _, turns, invocations = conversation_repositories
    identity = _identity("scope", conversation="shared-conversation")
    turns.append_turn(_scope(identity), _turn(identity))
    wrong_scope = ConversationScope(
        _identity(tenant="tenant-b").tenant_id,
        identity.user_id,
        identity.conversation_id,
    )
    with pytest.raises(ConversationAccessDenied):
        turns.list_turns(wrong_scope)

    command = _invocation(identity)
    assert isinstance(invocations.put_queued(command), AdmissionCreated)
    with pytest.raises(ConversationAccessDenied):
        invocations.get(
            identity.invocation_key, tenant_id="tenant-b", user_id="user-a",
        )


def test_invocation_replay_conflict_and_admission_cas_use_one_contract(
    conversation_repositories,
):
    _, turns, invocations = conversation_repositories
    identity = _identity("invocation", conversation="conv-invocation")
    turns.append_turn(_scope(identity), _turn(identity))
    command = _invocation(identity)

    assert isinstance(invocations.put_queued(command), AdmissionCreated)
    assert isinstance(invocations.put_queued(command), AdmissionExisting)
    changed_record = AdmissionRecord(
        **{**command.record.__dict__, "request_fingerprint": "b" * 64},
    )
    assert isinstance(invocations.put_queued(
        InvocationToCreate(**{**command.__dict__, "record": changed_record}),
    ), AdmissionConflict)

    pointer = ExecutionPointer("compat", "v1", str(identity.workflow_run_id))
    applied = invocations.compare_and_set(
        identity.invocation_key,
        expected_status=AdmissionStatus.START_QUEUED,
        expected_version=0,
        target_status=AdmissionStatus.EXECUTION_BOUND,
        execution_pointer=pointer,
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
    )
    assert isinstance(applied, CasApplied)
    assert applied.record.version == 1
    replay = invocations.compare_and_set(
        identity.invocation_key,
        expected_status=AdmissionStatus.START_QUEUED,
        expected_version=0,
        target_status=AdmissionStatus.EXECUTION_BOUND,
        execution_pointer=pointer,
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
    )
    assert isinstance(replay, CasAlreadyApplied)


def _invocation(identity):
    created_at = datetime.now(timezone.utc).isoformat()
    return InvocationToCreate(
        record=AdmissionRecord(
            invocation_key=identity.invocation_key,
            workflow_run_id=identity.workflow_run_id,
            request_fingerprint=request_fingerprint({
                "message": "hello", "tenant_id": str(identity.tenant_id),
            }),
            status=AdmissionStatus.START_QUEUED,
            version=0,
            pinned_versions={"bundle": "v1", "runtime": "compat-v1"},
        ),
        scope=_scope(identity),
        request_id=identity.request_id,
        continuation_id=identity.continuation_id,
        inbound_turn_key=identity.turn_key,
        created_at=created_at,
    )
