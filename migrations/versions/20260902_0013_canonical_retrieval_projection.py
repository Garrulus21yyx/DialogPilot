"""Add canonical retrieval projection outbox and deletion fences.

Revision ID: 20260902_0013
Revises: 20260902_0012
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0013"
down_revision: Union[str, Sequence[str], None] = "20260902_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = (
    "location:retrieval-projection-outbox:v1",
    "location:knowledge-index:v1",
    "location:episode-index:v1",
)

REGISTRY_FINGERPRINT = "2aae62ba01ac4195ae50a7dbd7b619f433d5a800b3fce8698a1e3a9a3f49f502"


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version, artifact_fingerprint, artifact_path, approved_by
        ) VALUES (
            'v4', '{REGISTRY_FINGERPRINT}',
            'governance/data_locations/v4.json',
            'canonical-retrieval-projection-review'
        )
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_source_chunk_specs (
            candidate_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            locale TEXT NOT NULL,
            product TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            source_checksum TEXT NOT NULL CHECK(source_checksum ~ '^[0-9a-f]{64}$'),
            start_char INTEGER NOT NULL CHECK(start_char >= 0),
            end_char INTEGER NOT NULL CHECK(end_char > start_char),
            provenance_sha256 TEXT NOT NULL CHECK(provenance_sha256 ~ '^[0-9a-f]{64}$'),
            embedding vector,
            lexical_document TEXT NOT NULL,
            immutable_fingerprint TEXT NOT NULL CHECK(immutable_fingerprint ~ '^[0-9a-f]{64}$'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            FOREIGN KEY (tenant_id, backend_id, generation_id, scope, locale, product,
                         source_id, revision_id)
                REFERENCES retrieval.knowledge_source_manifest_entries
                (tenant_id, backend_id, generation_id, scope, locale, product,
                 source_id, revision_id),
            UNIQUE (tenant_id, backend_id, generation_id, source_id, revision_id,
                    start_char, end_char)
        )
    """)
    op.execute("""
        CREATE TRIGGER knowledge_source_chunk_specs_immutable
        BEFORE UPDATE ON retrieval.knowledge_source_chunk_specs
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE TRIGGER knowledge_source_chunk_specs_generation_fence
        BEFORE INSERT ON retrieval.knowledge_source_chunk_specs
        FOR EACH ROW EXECUTE FUNCTION retrieval.guard_knowledge_manifest_insert()
    """)
    op.execute("""
        CREATE TABLE retrieval.canonical_projection_outbox (
            event_id TEXT PRIMARY KEY,
            corpus TEXT NOT NULL CHECK(corpus IN ('KNOWLEDGE', 'SERVICE_EPISODE')),
            tenant_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL CHECK(source_fingerprint ~ '^[0-9a-f]{64}$'),
            subject_user_id TEXT,
            subject_conversation_id TEXT,
            deletion_epoch BIGINT NOT NULL CHECK(deletion_epoch >= 0),
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(status IN ('PENDING', 'PROCESSING', 'APPLIED', 'REJECTED')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            result_code TEXT,
            lease_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            FOREIGN KEY (corpus, backend_id, generation_id)
                REFERENCES retrieval.retrieval_generation_registry
                (corpus, backend_id, generation_id),
            CHECK((subject_user_id IS NULL) = (subject_conversation_id IS NULL)),
            CHECK(
                (corpus='KNOWLEDGE' AND subject_user_id IS NULL AND deletion_epoch=0)
                OR
                (corpus='SERVICE_EPISODE' AND subject_user_id IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX canonical_projection_outbox_pending_idx
        ON retrieval.canonical_projection_outbox (status, created_at, event_id)
    """)
    op.execute("""
        CREATE TABLE retrieval.canonical_projection_receipts (
            event_id TEXT PRIMARY KEY REFERENCES retrieval.canonical_projection_outbox(event_id),
            result_code TEXT NOT NULL,
            candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
            projection_sha256 TEXT NOT NULL CHECK(projection_sha256 ~ '^[0-9a-f]{64}$'),
            completed_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
        )
    """)
    op.execute("""
        CREATE TRIGGER canonical_projection_receipts_immutable
        BEFORE UPDATE ON retrieval.canonical_projection_receipts
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_canonical_projection_enqueue()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,retrieval,dialogpilot_app AS $$
        DECLARE generation_state TEXT; current_epoch BIGINT; tombstoned_at TIMESTAMPTZ;
        BEGIN
            SELECT state INTO generation_state
              FROM retrieval.retrieval_generation_registry
             WHERE corpus=NEW.corpus AND backend_id=NEW.backend_id
               AND generation_id=NEW.generation_id FOR SHARE;
            IF generation_state IS DISTINCT FROM 'BUILDING' THEN
                RAISE EXCEPTION 'canonical projection requires BUILDING generation'
                    USING ERRCODE='55000';
            END IF;
            IF NEW.corpus='SERVICE_EPISODE' THEN
                SELECT deletion_epoch, deleted_at INTO current_epoch, tombstoned_at
                  FROM dialogpilot_app.conversations
                 WHERE tenant_id=NEW.tenant_id AND user_id=NEW.subject_user_id
                   AND conversation_id=NEW.subject_conversation_id FOR SHARE;
                IF NOT FOUND OR tombstoned_at IS NOT NULL
                   OR current_epoch <> NEW.deletion_epoch THEN
                    RAISE EXCEPTION 'canonical projection subject is deletion-fenced'
                        USING ERRCODE='55000';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER canonical_projection_enqueue_fence
        BEFORE INSERT ON retrieval.canonical_projection_outbox
        FOR EACH ROW EXECUTE FUNCTION retrieval.guard_canonical_projection_enqueue()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.purge_deleted_subject_projections()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,retrieval,dialogpilot_app AS $$
        BEGIN
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
                DELETE FROM retrieval.service_episode_search
                 WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
                   AND source_conversation_id=NEW.conversation_id;
                UPDATE retrieval.canonical_projection_outbox
                   SET status='REJECTED', result_code='SUBJECT_DELETION_FENCED',
                       lease_until=NULL, updated_at=transaction_timestamp()
                 WHERE corpus='SERVICE_EPISODE' AND tenant_id=NEW.tenant_id
                   AND subject_user_id=NEW.user_id
                   AND subject_conversation_id=NEW.conversation_id
                   AND status IN ('PENDING','PROCESSING');
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER conversation_retrieval_projection_delete
        AFTER UPDATE OF deleted_at ON dialogpilot_app.conversations
        FOR EACH ROW EXECUTE FUNCTION retrieval.purge_deleted_subject_projections()
    """)
    op.execute("""
        GRANT SELECT ON retrieval.knowledge_source_chunk_specs,
            retrieval.canonical_projection_outbox,
            retrieval.canonical_projection_receipts TO dialogpilot_retrieval
    """)


def downgrade() -> None:
    raise RuntimeError("canonical retrieval projection downgrade requires forward-fix")
