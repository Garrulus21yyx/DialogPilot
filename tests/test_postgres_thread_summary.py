"""M4-T02A canonical thread-summary schema and deletion owner proofs."""
import psycopg
import pytest
from psycopg.types.json import Jsonb

from application.data_location_registry import DataLocationRegistry, default_registry_path
from application.inbound_admission import NewInvocationInbound
from application.conversation_projection import ConversationSubject
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_projection import PostgresConversationDeletionRepository


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
