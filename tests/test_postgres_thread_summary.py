"""M4-T02 canonical thread-summary owner, CAS and rebuild proofs."""

import psycopg
import pytest
from psycopg.types.json import Jsonb

from application.data_location_registry import DataLocationRegistry, default_registry_path
from application.inbound_admission import NewInvocationInbound
from application.conversation_projection import ConversationSubject, ProjectionName
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_projection import (
    ConversationProjectionDispatcher,
    PostgresConversationDeletionRepository,
    PostgresConversationProjectionOutbox,
)
from infrastructure.postgres_thread_summary import PostgresThreadSummaryRepository
from application.thread_summary import (
    ThreadSummaryApplyStatus,
    ThreadSummaryConflict,
    ThreadSummaryPolicy,
    ThreadSummaryProjectionStatus,
    ThreadSummaryProjector,
    ThreadSummaryState,
)


CREATED = "2026-09-02T20:00:00+00:00"


@pytest.fixture()
def summary_scope(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=3,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.thread_summary_checkpoints,
                dialogpilot_app.thread_summary_chunks,
                dialogpilot_app.projection_watermarks,
                dialogpilot_app.conversation_projection_outbox,
                dialogpilot_app.workflow_start_outbox,
                dialogpilot_app.workflow_invocations,
                dialogpilot_app.conversation_events,
                dialogpilot_app.conversation_turns,
                dialogpilot_app.conversations
            CASCADE
        """)
    identity = IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-summary", user_id="user-summary",
        conversation_id="conversation-summary", request_id="request-summary",
    )
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity, "source turn", {"bundle": "v1"}, CREATED,
    ))
    try:
        yield pool, identity
    finally:
        pool.close()


def _insert_summary(pool, identity, *, epoch=0):
    scope = (
        str(identity.tenant_id), str(identity.user_id),
        str(identity.conversation_id),
    )
    with pool.transaction() as connection:
        connection.execute("""
            INSERT INTO dialogpilot_app.thread_summary_chunks (
                chunk_id, tenant_id, user_id, conversation_id, from_seq, to_seq,
                source_sha256, summary, included_ranges, omitted_ranges,
                summarizer_version, schema_version, source_deletion_epoch
            ) VALUES ('chunk-1',%s,%s,%s,1,1,%s,'summary',%s,%s,
                      'summarizer-v1','thread-summary-v1',%s)
        """, (*scope, "a" * 64, Jsonb([[1, 1]]), Jsonb([]), epoch))
        connection.execute("""
            INSERT INTO dialogpilot_app.thread_summary_checkpoints (
                tenant_id, user_id, conversation_id, source_watermark,
                projection_watermark, last_chunk_id, state, expected_version,
                source_deletion_epoch
            ) VALUES (%s,%s,%s,1,1,'chunk-1','READY',1,%s)
        """, (*scope, epoch))


def _admit_more(pool, identity, count):
    for index in range(count):
        next_identity = IdentityFactory(lambda: "unused").create_invocation(
            tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
            conversation_id=str(identity.conversation_id),
            request_id=f"request-summary-{index + 2}",
        )
        PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
            next_identity, f"source turn {index + 2}", {"bundle": "v1"}, CREATED,
        ))


def test_thread_summary_location_is_write_approved_with_proof_and_delete_adapter():
    location = DataLocationRegistry.load(default_registry_path()).get(
        "location:thread-summary:v1"
    )
    assert location.readiness.value == "WRITE_APPROVED"
    assert location.proof_contract_id == "proof:m4-t02-thread-summary:v1"
    assert location.delete_adapter_id == "adapter:pg-thread-summary-delete:v1"


def test_chunk_is_immutable_and_checkpoint_points_to_committed_range(summary_scope):
    pool, identity = summary_scope
    _insert_summary(pool, identity)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        with pool.transaction() as connection:
            connection.execute("""
                UPDATE dialogpilot_app.thread_summary_chunks
                SET summary='changed' WHERE chunk_id='chunk-1'
            """)
    with pool.transaction() as connection:
        assert connection.execute("""
            SELECT source_watermark, projection_watermark, last_chunk_id,
                   state, expected_version
            FROM dialogpilot_app.thread_summary_checkpoints
        """).fetchone() == (1, 1, "chunk-1", "READY", 1)


def test_stale_epoch_write_is_fenced_and_deletion_purges_chunks_and_checkpoint(
    summary_scope,
):
    pool, identity = summary_scope
    _insert_summary(pool, identity)
    PostgresConversationDeletionRepository(pool).delete(
        ConversationSubject(
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        ),
        reason_code="privacy", actor="privacy-worker", created_at=CREATED,
    )
    with pool.transaction() as connection:
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.thread_summary_chunks"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.thread_summary_checkpoints"
        ).fetchone()[0] == 0
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="deletion-fenced",
    ):
        _insert_summary(pool, identity, epoch=0)


def test_fixed_range_prepare_commit_and_replay_are_contiguous(summary_scope):
    pool, identity = summary_scope
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )
    repository = PostgresThreadSummaryRepository(pool)
    job = repository.prepare(subject, summarizer_version="summarizer-v1")
    assert (job.from_seq, job.to_seq, job.expected_version) == (1, 1, 0)
    assert job.included_ranges == ((1, 1),)
    assert repository.commit(
        job, summary="用户需要处理 source turn。",
    ) is ThreadSummaryApplyStatus.APPLIED
    assert repository.commit(
        job, summary="用户需要处理 source turn。",
    ) is ThreadSummaryApplyStatus.ALREADY_APPLIED
    assert repository.prepare(subject, summarizer_version="summarizer-v1") is None
    view = repository.read(subject)
    assert (view.state, view.projection_watermark, view.version) == ("READY", 1, 1)
    assert view.conflicts == ()


def test_checkpoint_cas_rejects_competing_candidate_content(summary_scope):
    pool, identity = summary_scope
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )
    repository = PostgresThreadSummaryRepository(pool)
    first = repository.prepare(subject, summarizer_version="summarizer-v1")
    competing = repository.prepare(subject, summarizer_version="summarizer-v1")
    repository.commit(first, summary="first")
    with pytest.raises(ThreadSummaryConflict):
        repository.commit(competing, summary="different")


def test_chunk_and_checkpoint_commit_roll_back_together_on_fault(summary_scope):
    pool, identity = summary_scope
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )

    def fail(stage):
        if stage == "after_summary_chunk":
            raise RuntimeError("crash")

    repository = PostgresThreadSummaryRepository(pool, fault_hook=fail)
    job = repository.prepare(subject, summarizer_version="summarizer-v1")
    with pytest.raises(RuntimeError, match="crash"):
        repository.commit(job, summary="candidate")
    with pool.transaction() as connection:
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.thread_summary_chunks"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.thread_summary_checkpoints"
        ).fetchone()[0] == 0


def test_reader_exposes_corrupt_source_hash_instead_of_hiding_range(summary_scope):
    pool, identity = summary_scope
    _insert_summary(pool, identity)
    view = PostgresThreadSummaryRepository(pool).read(ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    ))
    assert view.state == "DEGRADED"
    assert view.conflicts == ("source_hash:1-1",)


class _Summarizer:
    version = "summarizer-v1"

    def __init__(self, *, failure=False):
        self.failure = failure
        self.calls = 0

    def summarize(self, items):
        self.calls += 1
        if self.failure:
            raise TimeoutError("model backend unavailable")
        return " | ".join(item.content for item in items if item.content)


def test_policy_skips_per_event_calls_but_explicit_rebuild_processes_fixed_range(
    summary_scope,
):
    pool, identity = summary_scope
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )
    summarizer = _Summarizer()
    projector = ThreadSummaryProjector(
        PostgresThreadSummaryRepository(pool), summarizer,
        policy=ThreadSummaryPolicy(message_threshold=2, token_threshold=100),
    )
    assert projector.project_subject(subject) is (
        ThreadSummaryProjectionStatus.POLICY_SKIPPED
    )
    assert summarizer.calls == 0
    assert projector.project_subject(subject, force=True) is (
        ThreadSummaryProjectionStatus.APPLIED
    )
    assert summarizer.calls == 1


def test_model_unavailable_marks_only_summary_degraded_and_keeps_l0(summary_scope):
    pool, identity = summary_scope
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )
    repository = PostgresThreadSummaryRepository(pool)
    result = ThreadSummaryProjector(
        repository, _Summarizer(failure=True),
        policy=ThreadSummaryPolicy(message_threshold=1),
    ).project_subject(subject)
    assert result is ThreadSummaryProjectionStatus.DEGRADED
    view = repository.read(subject)
    assert (view.state, view.source_watermark, view.projection_watermark) == (
        "DEGRADED", 1, 0,
    )
    with pool.transaction() as connection:
        assert connection.execute("""
            SELECT count(*) FROM dialogpilot_app.conversation_events
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (subject.tenant_id, subject.user_id, subject.conversation_id)).fetchone()[0] == 1


def test_corrupt_generation_is_rebuilt_from_raw_l0_without_summary_chaining(
    summary_scope,
):
    pool, identity = summary_scope
    _insert_summary(pool, identity)
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )
    summarizer = _Summarizer()
    repository = PostgresThreadSummaryRepository(pool)
    repaired = ThreadSummaryProjector(
        repository, summarizer, policy=ThreadSummaryPolicy(message_threshold=1),
    ).repair_if_corrupt(subject)
    assert (repaired.state, repaired.generation) == ("READY", 2)
    assert repaired.summaries == ("source turn",)
    assert repaired.conflicts == ()
    with pool.transaction() as connection:
        assert connection.execute("""
            SELECT count(DISTINCT generation)
            FROM dialogpilot_app.thread_summary_chunks
        """).fetchone()[0] == 2


def test_thread_summary_state_algebra_is_closed():
    assert set(ThreadSummaryState)


def test_thread_summary_projector_consumes_canonical_conversation_outbox(
    summary_scope,
):
    pool, identity = summary_scope
    projector = ThreadSummaryProjector(
        PostgresThreadSummaryRepository(pool), _Summarizer(),
        policy=ThreadSummaryPolicy(message_threshold=1),
    )
    result = ConversationProjectionDispatcher(
        outbox=PostgresConversationProjectionOutbox(pool),
        deletion=PostgresConversationDeletionRepository(pool),
        adapters={ProjectionName.THREAD_SUMMARY: projector},
    ).dispatch_once(
        projection_name=ProjectionName.THREAD_SUMMARY,
        worker_id="thread-summary-worker",
        now=CREATED,
        lease_until="2026-09-02T20:01:00+00:00",
        retry_at="2026-09-02T20:02:00+00:00",
    )
    assert [item.status for item in result] == ["APPLIED"]
    view = PostgresThreadSummaryRepository(pool).read(ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    ))
    assert view.state is ThreadSummaryState.READY


@pytest.mark.parametrize("job_bound", [1, 2, 3])
def test_generated_job_bounds_cover_every_raw_event_without_gap(
    summary_scope, job_bound,
):
    pool, identity = summary_scope
    _admit_more(pool, identity, 4)
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )
    view = ThreadSummaryProjector(
        PostgresThreadSummaryRepository(pool), _Summarizer(),
        policy=ThreadSummaryPolicy(
            message_threshold=99, token_threshold=99_999,
            max_events_per_job=job_bound,
        ),
    ).rebuild(subject)
    assert (view.state, view.source_watermark, view.projection_watermark) == (
        ThreadSummaryState.READY, 5, 5,
    )
    assert len(view.summaries) == (5 + job_bound - 1) // job_bound
    flattened = [
        seq
        for start, end in view.included_ranges
        for seq in range(int(start), int(end) + 1)
    ]
    assert flattened == [1, 2, 3, 4, 5]


def test_rebuild_generation_fences_pre_rebuild_candidate(summary_scope):
    pool, identity = summary_scope
    subject = ConversationSubject(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
    )
    repository = PostgresThreadSummaryRepository(pool)
    stale = repository.prepare(subject, summarizer_version="summarizer-v1")
    assert repository.start_rebuild(subject) == 2
    with pytest.raises(ThreadSummaryConflict, match="checkpoint CAS"):
        repository.commit(stale, summary="first-generation")
