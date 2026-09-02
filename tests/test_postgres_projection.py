"""M1-T04 projection outbox, replay and deletion-fence properties."""
from dataclasses import dataclass, field

import psycopg
import pytest

from application.conversation_projection import (
    ConversationSubject,
    ProjectionApplyStatus,
    ProjectionName,
)
from application.conversation_store import ConversationScope, EventToAppend
from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_conversation import PostgresConversationTurnStore
from infrastructure.postgres_projection import (
    ConversationProjectionDispatcher,
    PostgresConversationDeletionRepository,
    PostgresConversationProjectionOutbox,
    ProjectionClaimError,
)


CREATED = "2026-09-02T09:00:00+00:00"


@pytest.fixture()
def projection_components(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=5,
    ))
    pool.open()
    _reset(pool)
    identity = _identity("one")
    _seed(pool, identity)
    try:
        yield (
            pool, identity,
            PostgresConversationProjectionOutbox(pool),
            PostgresConversationDeletionRepository(pool),
        )
    finally:
        pool.close()


def _identity(suffix, conversation="projection-conversation"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-projection",
        user_id="user-projection",
        conversation_id=conversation,
        request_id=f"request-{suffix}",
    )


def _subject(identity):
    return ConversationSubject(
        str(identity.tenant_id), str(identity.user_id),
        str(identity.conversation_id),
    )


def _seed(pool, identity, *, fault_hook=None):
    return PostgresAdmissionUnitOfWork(pool, fault_hook=fault_hook).admit_new(
        NewInvocationInbound(
            identity=identity,
            message="question",
            pinned_versions={"bundle": "v1", "runtime": "compat-v1"},
            created_at=CREATED,
        )
    )


def _reset(pool):
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.projection_watermarks,
                dialogpilot_app.conversation_projection_outbox,
                dialogpilot_app.conversation_deletion_events,
                dialogpilot_app.publication_revision_events,
                dialogpilot_app.delivery_receipts,
                dialogpilot_app.delivery_outbox,
                dialogpilot_app.resume_requested_outbox,
                dialogpilot_app.workflow_start_outbox,
                dialogpilot_app.response_deliveries,
                dialogpilot_app.workflow_invocations,
                dialogpilot_app.conversation_events,
                dialogpilot_app.conversation_turns,
                dialogpilot_app.conversations
            CASCADE
        """)
        connection.execute("""
            UPDATE dialogpilot_app.projection_registry
            SET generation=1, policy_version='conversation-projection-v1',
                enabled=TRUE, updated_at=transaction_timestamp()
        """)


@dataclass
class MemoryProjectionAdapter:
    effects: dict[str, set[str]] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    delete_epochs: list[int] = field(default_factory=list)
    fail: bool = False
    on_apply: object = None

    def apply(self, event):
        if self.fail:
            raise RuntimeError("projection backend unavailable")
        callback = self.on_apply
        if callback is not None:
            self.on_apply = None
            callback()
        if event.operation_key in self.seen:
            return ProjectionApplyStatus.ALREADY_APPLIED
        key = _subject_key(event.subject)
        self.effects.setdefault(key, set()).add(event.operation_key)
        self.seen.add(event.operation_key)
        return ProjectionApplyStatus.APPLIED

    def delete_subject(self, subject, *, through_deletion_epoch):
        self.effects.pop(_subject_key(subject), None)
        self.delete_epochs.append(through_deletion_epoch)


def _subject_key(subject):
    return f"{subject.tenant_id}/{subject.user_id}/{subject.conversation_id}"


def _dispatcher(outbox, deletion, adapter, *, fault_hook=None):
    return ConversationProjectionDispatcher(
        outbox=outbox,
        deletion=deletion,
        adapters={ProjectionName.WORKING_WINDOW: adapter,
                  ProjectionName.EPISODIC_INDEX: adapter},
        fault_hook=fault_hook,
    )


def _dispatch(dispatcher, projection=ProjectionName.WORKING_WINDOW, now=CREATED):
    return dispatcher.dispatch_once(
        projection_name=projection,
        worker_id="projection-worker",
        now=now,
        lease_until="2026-09-02T09:01:00+00:00",
        retry_at="2026-09-02T09:02:00+00:00",
        limit=20,
    )


def test_each_source_event_atomically_enqueues_all_registered_projections(
    projection_components,
):
    pool, _, _, _ = projection_components
    with pool.transaction() as connection:
        source_count = connection.execute(
            "SELECT count(*) FROM dialogpilot_app.conversation_events"
        ).fetchone()[0]
        outboxes = connection.execute("""
            SELECT projection_name, event_seq, source_deletion_epoch
            FROM dialogpilot_app.conversation_projection_outbox
            ORDER BY projection_name
        """).fetchall()
    assert source_count == 1
    assert outboxes == [
        ("episodic_index", 1, 0),
        ("fact_extraction", 1, 0),
        ("thread_summary", 1, 0),
        ("working_window", 1, 0),
    ]


def test_source_transaction_failure_leaves_no_event_or_projection_outbox(
    postgres_database_url,
):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    pool.open()
    _reset(pool)

    def fail(stage):
        if stage == "after_accepted_event":
            raise RuntimeError("crash after source event")

    try:
        with pytest.raises(RuntimeError, match="crash after source event"):
            _seed(pool, _identity("rollback", "projection-rollback"), fault_hook=fail)
        with pool.transaction() as connection:
            counts = tuple(connection.execute(
                f"SELECT count(*) FROM dialogpilot_app.{table}",
            ).fetchone()[0] for table in (
                "conversation_events", "conversation_projection_outbox",
            ))
        assert counts == (0, 0)
    finally:
        pool.close()


def test_projection_failure_retries_without_rolling_back_source_or_duplicating_effect(
    projection_components,
):
    pool, identity, outbox, deletion = projection_components
    adapter = MemoryProjectionAdapter(fail=True)
    dispatcher = _dispatcher(outbox, deletion, adapter)
    first = _dispatch(dispatcher)
    assert first[0].status == "RETRY"
    with pool.transaction() as connection:
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.conversation_events"
        ).fetchone()[0] == 1
    adapter.fail = False
    second = _dispatch(
        dispatcher, now="2026-09-02T09:03:00+00:00",
    )
    assert second[0].status == "APPLIED"
    assert len(adapter.effects[_subject_key(_subject(identity))]) == 1
    with pool.transaction() as connection:
        watermark = connection.execute("""
            SELECT last_event_seq FROM dialogpilot_app.projection_watermarks
            WHERE projection_name='working_window' AND generation=1
        """).fetchone()[0]
    assert watermark == 1


def test_crash_after_external_effect_replays_same_operation_idempotently(
    projection_components,
):
    _, identity, outbox, deletion = projection_components
    adapter = MemoryProjectionAdapter()
    injected = {"done": False}

    def fail_once(stage):
        if stage == "after_projection_effect" and not injected["done"]:
            injected["done"] = True
            raise RuntimeError("crash after effect")

    first = _dispatch(_dispatcher(
        outbox, deletion, adapter, fault_hook=fail_once,
    ))
    assert first[0].status == "RETRY"
    second = _dispatch(
        _dispatcher(outbox, deletion, adapter),
        now="2026-09-02T09:03:00+00:00",
    )
    assert second[0].status == "ALREADY_APPLIED"
    assert len(adapter.effects[_subject_key(_subject(identity))]) == 1


def test_crash_after_projection_ack_is_known_applied_and_not_reclaimed(
    projection_components,
):
    _, _, outbox, deletion = projection_components
    adapter = MemoryProjectionAdapter()

    def fail_after_ack(stage):
        if stage == "after_projection_ack":
            raise RuntimeError("process exit after ACK")

    first = _dispatch(_dispatcher(
        outbox, deletion, adapter, fault_hook=fail_after_ack,
    ))
    assert first[0].status == "APPLIED"
    assert first[0].error_type == "RuntimeError"
    assert _dispatch(
        _dispatcher(outbox, deletion, adapter),
        now="2026-09-02T09:03:00+00:00",
    ) == ()


def test_projection_policy_keeps_interaction_out_of_episode_and_fact_surfaces(
    projection_components,
):
    pool, identity, outbox, deletion = projection_components
    scope = ConversationScope(
        identity.tenant_id, identity.user_id, identity.conversation_id,
    )
    PostgresConversationTurnStore(pool).append_event(scope, EventToAppend(
        event_id="interaction-event",
        operation_key=identity.operation_key(
            "ProjectionTest", "interaction", "signal-1",
        ),
        event_type="INTERACTION_REQUEST_PUBLISHED",
        payload={"projection_disposition": "approval"},
        created_at="2026-09-02T09:00:01+00:00",
        invocation_key=identity.invocation_key,
    ))
    adapter = MemoryProjectionAdapter()
    dispatcher = _dispatcher(outbox, deletion, adapter)
    assert _dispatch(dispatcher, ProjectionName.EPISODIC_INDEX)[0].status == "APPLIED"
    skipped = _dispatch(
        dispatcher, ProjectionName.EPISODIC_INDEX,
        now="2026-09-02T09:00:02+00:00",
    )
    assert skipped[0].status == "POLICY_SKIPPED"
    assert len(adapter.effects[_subject_key(_subject(identity))]) == 1


def test_deletion_during_external_write_removes_stale_effect_and_fences_late_writes(
    projection_components,
):
    pool, identity, outbox, deletion = projection_components
    adapter = MemoryProjectionAdapter()
    adapter.on_apply = lambda: deletion.delete(
        _subject(identity), reason_code="user_erasure", actor="privacy-worker",
        created_at="2026-09-02T09:00:02+00:00",
    )
    result = _dispatch(_dispatcher(outbox, deletion, adapter))
    assert result[0].status == "DELETION_FENCED"
    assert adapter.effects == {}
    assert adapter.delete_epochs == [1]

    late_identity = _identity("late")
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="deletion-fenced",
    ):
        _seed(pool, late_identity)
    tombstone = _dispatch(
        _dispatcher(outbox, deletion, adapter),
        now="2026-09-02T09:00:03+00:00",
    )
    assert tombstone[0].status == "DELETION_FENCED"
    assert adapter.effects == {}


def test_rebuild_creates_new_generation_and_replays_source_events(
    projection_components,
):
    pool, _, outbox, deletion = projection_components
    generation = outbox.rebuild(
        ProjectionName.WORKING_WINDOW,
        policy_version="conversation-projection-v1-rebuild",
        requested_at="2026-09-02T09:10:00+00:00",
    )
    assert generation == 2
    adapter = MemoryProjectionAdapter()
    dispatcher = _dispatcher(outbox, deletion, adapter)
    first = _dispatch(dispatcher, now="2026-09-02T09:10:01+00:00")
    assert len(first) == 2
    assert {result.status for result in first} == {"APPLIED"}
    second = _dispatch(dispatcher, now="2026-09-02T09:10:02+00:00")
    assert second == ()
    with pool.transaction() as connection:
        generations = connection.execute("""
            SELECT generation, last_event_seq
            FROM dialogpilot_app.projection_watermarks
            WHERE projection_name='working_window'
            ORDER BY generation
        """).fetchall()
    assert generations == [(1, 1), (2, 1)]


def test_missing_adapter_fails_before_claim_and_preserves_due_item(
    projection_components,
):
    pool, _, outbox, deletion = projection_components
    dispatcher = ConversationProjectionDispatcher(
        outbox=outbox, deletion=deletion, adapters={},
    )
    with pytest.raises(ProjectionClaimError, match="adapter is unavailable"):
        _dispatch(dispatcher)
    with pool.transaction() as connection:
        row = connection.execute("""
            SELECT attempt, claimed_by, acknowledged_at
            FROM dialogpilot_app.conversation_projection_outbox
            WHERE projection_name='working_window'
        """).fetchone()
    assert row == (0, None, None)
