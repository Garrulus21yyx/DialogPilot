"""Add canonical thread-summary chunks and checkpoints.

Revision ID: 20260902_0018
Revises: 20260902_0017
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0018"
down_revision: Union[str, Sequence[str], None] = "20260902_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = ("location:thread-summary:v1",)

REGISTRY_FINGERPRINT = "cd86f3a57adaaa2e25fb4cc6c9e59fb64ebc43f12243463ca83335198382a1e5"


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version, artifact_fingerprint, artifact_path, approved_by
        ) VALUES (
            'v5', '{REGISTRY_FINGERPRINT}',
            'governance/data_locations/v5.json',
            'm4-t02-thread-summary-owner-review'
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.thread_summary_chunks (
            chunk_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            from_seq BIGINT NOT NULL CHECK(from_seq >= 1),
            to_seq BIGINT NOT NULL CHECK(to_seq >= from_seq),
            source_sha256 TEXT NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
            summary TEXT NOT NULL,
            included_ranges JSONB NOT NULL,
            omitted_ranges JSONB NOT NULL,
            summarizer_version TEXT NOT NULL CHECK(length(btrim(summarizer_version)) > 0),
            schema_version TEXT NOT NULL CHECK(length(btrim(schema_version)) > 0),
            source_deletion_epoch BIGINT NOT NULL CHECK(source_deletion_epoch >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id),
            UNIQUE (tenant_id, user_id, conversation_id, from_seq, to_seq,
                    summarizer_version, schema_version)
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.thread_summary_checkpoints (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            source_watermark BIGINT NOT NULL DEFAULT 0 CHECK(source_watermark >= 0),
            projection_watermark BIGINT NOT NULL DEFAULT 0
                CHECK(projection_watermark >= 0),
            last_chunk_id TEXT REFERENCES dialogpilot_app.thread_summary_chunks(chunk_id),
            state TEXT NOT NULL CHECK(state IN ('READY','LAGGING','DEGRADED')),
            expected_version BIGINT NOT NULL DEFAULT 0 CHECK(expected_version >= 0),
            source_deletion_epoch BIGINT NOT NULL CHECK(source_deletion_epoch >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id, user_id, conversation_id),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id),
            CHECK(projection_watermark <= source_watermark),
            CHECK((projection_watermark=0) = (last_chunk_id IS NULL))
        )
    """)
    op.execute("""
        CREATE TRIGGER thread_summary_chunks_immutable
        BEFORE UPDATE ON dialogpilot_app.thread_summary_chunks
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_app.guard_thread_summary_subject()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_epoch BIGINT; tombstone TIMESTAMPTZ;
        BEGIN
            SELECT deletion_epoch, deleted_at INTO current_epoch, tombstone
            FROM dialogpilot_app.conversations
            WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
              AND conversation_id=NEW.conversation_id FOR SHARE;
            IF NOT FOUND OR tombstone IS NOT NULL
               OR current_epoch <> NEW.source_deletion_epoch THEN
                RAISE EXCEPTION 'thread summary subject is deletion-fenced'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER thread_summary_chunk_subject_fence
        BEFORE INSERT ON dialogpilot_app.thread_summary_chunks
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_app.guard_thread_summary_subject()
    """)
    op.execute("""
        CREATE TRIGGER thread_summary_checkpoint_subject_fence
        BEFORE INSERT OR UPDATE ON dialogpilot_app.thread_summary_checkpoints
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_app.guard_thread_summary_subject()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_app.purge_thread_summary_on_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
                DELETE FROM dialogpilot_app.thread_summary_checkpoints
                WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
                  AND conversation_id=NEW.conversation_id;
                DELETE FROM dialogpilot_app.thread_summary_chunks
                WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
                  AND conversation_id=NEW.conversation_id;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER conversation_thread_summary_delete
        AFTER UPDATE OF deleted_at ON dialogpilot_app.conversations
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_app.purge_thread_summary_on_delete()
    """)


def downgrade() -> None:
    raise RuntimeError("Thread summary downgrade requires forward-fix")
