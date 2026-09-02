"""Add canonical ServiceEpisode revisions and current-head CAS.

Revision ID: 20260902_0020
Revises: 20260902_0019
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0020"
down_revision: Union[str, Sequence[str], None] = "20260902_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = ("location:service-episode:v1",)

REGISTRY_FINGERPRINT = "cbf99367f299650413996974cf981a58c8485d984d8df9cea7d0d0790babedf3"


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version, artifact_fingerprint, artifact_path, approved_by
        ) VALUES (
            'v6', '{REGISTRY_FINGERPRINT}',
            'governance/data_locations/v6.json',
            'm4-t04-service-episode-owner-review'
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.service_episode_revisions (
            episode_id TEXT NOT NULL,
            revision BIGINT NOT NULL CHECK(revision >= 1),
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            case_status TEXT NOT NULL CHECK(case_status IN ('resolved','closed')),
            problem TEXT NOT NULL CHECK(length(btrim(problem)) > 0),
            product_version TEXT,
            symptoms JSONB NOT NULL,
            materials JSONB NOT NULL,
            actions JSONB NOT NULL,
            authoritative_outcomes JSONB NOT NULL,
            resolution TEXT NOT NULL CHECK(length(btrim(resolution)) > 0),
            root_cause TEXT,
            outcome_verification_ref TEXT NOT NULL
                CHECK(length(btrim(outcome_verification_ref)) > 0),
            verified_at TIMESTAMPTZ NOT NULL,
            user_evidence JSONB NOT NULL,
            assistant_evidence JSONB NOT NULL,
            source_event_refs JSONB NOT NULL,
            provenance_sha256 TEXT NOT NULL CHECK(provenance_sha256 ~ '^[0-9a-f]{64}$'),
            extractor_version TEXT NOT NULL CHECK(length(btrim(extractor_version)) > 0),
            schema_version TEXT NOT NULL CHECK(schema_version='service-episode-v1'),
            source_deletion_epoch BIGINT NOT NULL CHECK(source_deletion_epoch >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (episode_id, revision),
            UNIQUE (case_id, revision),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id),
            CHECK(episode_id=case_id),
            CHECK(jsonb_typeof(symptoms)='array'),
            CHECK(jsonb_typeof(materials)='array'),
            CHECK(jsonb_typeof(actions)='array'),
            CHECK(jsonb_typeof(authoritative_outcomes)='array'),
            CHECK(jsonb_array_length(authoritative_outcomes) > 0),
            CHECK(jsonb_typeof(user_evidence)='array'),
            CHECK(jsonb_typeof(assistant_evidence)='array'),
            CHECK(jsonb_typeof(source_event_refs)='array'),
            CHECK(jsonb_array_length(source_event_refs) > 0)
        )
    """)
    op.execute("""
        DROP INDEX retrieval.service_episode_search_search_tsv_gin
    """)
    op.execute("""
        ALTER TABLE retrieval.service_episode_search
        ADD COLUMN user_lexical_document TEXT NOT NULL DEFAULT '',
        ADD COLUMN assistant_lexical_document TEXT NOT NULL DEFAULT ''
    """)
    op.execute("""
        ALTER TABLE retrieval.service_episode_search DROP COLUMN search_tsv
    """)
    op.execute("""
        ALTER TABLE retrieval.service_episode_search
        ADD COLUMN search_tsv TSVECTOR GENERATED ALWAYS AS (
            setweight(to_tsvector('simple', user_lexical_document), 'A') ||
            setweight(to_tsvector('simple', lexical_document), 'B') ||
            setweight(to_tsvector('simple', assistant_lexical_document), 'D')
        ) STORED
    """)
    op.execute("""
        CREATE INDEX service_episode_search_search_tsv_gin
        ON retrieval.service_episode_search USING GIN (search_tsv)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.service_episode_heads (
            episode_id TEXT PRIMARY KEY,
            current_revision BIGINT NOT NULL CHECK(current_revision >= 1),
            expected_version BIGINT NOT NULL CHECK(expected_version >= 1),
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            source_deletion_epoch BIGINT NOT NULL CHECK(source_deletion_epoch >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            FOREIGN KEY (episode_id, current_revision)
                REFERENCES dialogpilot_app.service_episode_revisions
                (episode_id, revision),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE TRIGGER service_episode_revisions_immutable
        BEFORE UPDATE ON dialogpilot_app.service_episode_revisions
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_app.guard_service_episode_subject()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_epoch BIGINT; tombstone TIMESTAMPTZ;
        BEGIN
            SELECT deletion_epoch, deleted_at INTO current_epoch, tombstone
            FROM dialogpilot_app.conversations
            WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
              AND conversation_id=NEW.conversation_id FOR SHARE;
            IF NOT FOUND OR tombstone IS NOT NULL
               OR current_epoch <> NEW.source_deletion_epoch THEN
                RAISE EXCEPTION 'service episode subject is deletion-fenced'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER service_episode_revision_subject_fence
        BEFORE INSERT ON dialogpilot_app.service_episode_revisions
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_app.guard_service_episode_subject()
    """)
    op.execute("""
        CREATE TRIGGER service_episode_head_subject_fence
        BEFORE INSERT OR UPDATE ON dialogpilot_app.service_episode_heads
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_app.guard_service_episode_subject()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_app.purge_service_episode_on_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
                DELETE FROM dialogpilot_app.service_episode_heads
                WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
                  AND conversation_id=NEW.conversation_id;
                DELETE FROM dialogpilot_app.service_episode_revisions
                WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
                  AND conversation_id=NEW.conversation_id;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER conversation_service_episode_delete
        AFTER UPDATE OF deleted_at ON dialogpilot_app.conversations
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_app.purge_service_episode_on_delete()
    """)


def downgrade() -> None:
    raise RuntimeError("ServiceEpisode downgrade requires forward-fix")
