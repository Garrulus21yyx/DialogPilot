"""Add the corpus-scoped retrieval generation and search schema.

Revision ID: 20260902_0010
Revises: 20260902_0009
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0010"
down_revision: Union[str, Sequence[str], None] = "20260902_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = (
    "location:knowledge-index:v1",
    "location:episode-index:v1",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='dialogpilot_retrieval') THEN
                CREATE ROLE dialogpilot_retrieval NOLOGIN;
            END IF;
            EXECUTE format('GRANT dialogpilot_retrieval TO %I', current_user);
        END $$
    """)
    op.execute("CREATE SCHEMA retrieval AUTHORIZATION dialogpilot_retrieval")
    op.execute("""
        CREATE TABLE retrieval.retrieval_generation_registry (
            corpus TEXT NOT NULL CHECK (corpus IN ('KNOWLEDGE', 'SERVICE_EPISODE')),
            backend_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            backend_fingerprint TEXT NOT NULL,
            immutable_fingerprint TEXT NOT NULL CHECK(length(immutable_fingerprint)=64),
            schema_version TEXT NOT NULL,
            source_watermark TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dimension INTEGER NOT NULL CHECK(embedding_dimension > 0),
            embedding_model_digest TEXT NOT NULL,
            replayable_across_environments BOOLEAN GENERATED ALWAYS AS
                (length(embedding_model_digest)=64) STORED,
            distance_metric TEXT NOT NULL CHECK(distance_metric='COSINE'),
            vector_extension_version TEXT NOT NULL,
            index_method TEXT NOT NULL CHECK(index_method IN ('EXACT', 'HNSW')),
            index_params JSONB NOT NULL CHECK(jsonb_typeof(index_params)='object'),
            chinese_tokenizer TEXT NOT NULL,
            lexical_ranker TEXT NOT NULL,
            manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),
            state TEXT NOT NULL CHECK(state IN (
                'REGISTERED', 'BUILDING', 'READY', 'ACTIVE', 'RETIRED', 'FAILED'
            )),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (corpus, backend_id, generation_id),
            UNIQUE (generation_id),
            UNIQUE (immutable_fingerprint)
        )
    """)
    op.execute("""
        CREATE TABLE retrieval.retrieval_generation_pointers (
            corpus TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            active_generation_id TEXT NOT NULL,
            previous_generation_id TEXT,
            version BIGINT NOT NULL CHECK(version > 0),
            changed_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (corpus, backend_id),
            FOREIGN KEY (corpus, backend_id, active_generation_id)
                REFERENCES retrieval.retrieval_generation_registry
                (corpus, backend_id, generation_id),
            FOREIGN KEY (corpus, backend_id, previous_generation_id)
                REFERENCES retrieval.retrieval_generation_registry
                (corpus, backend_id, generation_id),
            CHECK(previous_generation_id IS NULL OR
                  previous_generation_id <> active_generation_id)
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_generation_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(OLD.corpus, OLD.backend_id, OLD.generation_id,
                   OLD.backend_fingerprint, OLD.immutable_fingerprint,
                   OLD.schema_version, OLD.source_watermark,
                   OLD.embedding_model, OLD.embedding_dimension,
                   OLD.embedding_model_digest, OLD.distance_metric,
                   OLD.vector_extension_version, OLD.index_method,
                   OLD.index_params, OLD.chinese_tokenizer,
                   OLD.lexical_ranker, OLD.manifest_hash)
               IS DISTINCT FROM
               ROW(NEW.corpus, NEW.backend_id, NEW.generation_id,
                   NEW.backend_fingerprint, NEW.immutable_fingerprint,
                   NEW.schema_version, NEW.source_watermark,
                   NEW.embedding_model, NEW.embedding_dimension,
                   NEW.embedding_model_digest, NEW.distance_metric,
                   NEW.vector_extension_version, NEW.index_method,
                   NEW.index_params, NEW.chinese_tokenizer,
                   NEW.lexical_ranker, NEW.manifest_hash) THEN
                RAISE EXCEPTION 'retrieval generation definition is immutable'
                    USING ERRCODE='55000';
            END IF;
            IF NOT (
                (OLD.state='REGISTERED' AND NEW.state IN ('BUILDING', 'FAILED')) OR
                (OLD.state='BUILDING' AND NEW.state IN ('READY', 'FAILED')) OR
                (OLD.state='READY' AND NEW.state IN ('ACTIVE', 'FAILED')) OR
                (OLD.state='ACTIVE' AND NEW.state='RETIRED')
            ) THEN
                RAISE EXCEPTION 'illegal retrieval generation transition'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER retrieval_generation_transition_guard
        BEFORE UPDATE ON retrieval.retrieval_generation_registry
        FOR EACH ROW EXECUTE FUNCTION retrieval.guard_generation_transition()
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_chunk_search (
            candidate_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            corpus TEXT GENERATED ALWAYS AS ('KNOWLEDGE') STORED,
            backend_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            source_checksum TEXT NOT NULL CHECK(length(source_checksum)=64),
            source_span JSONB NOT NULL CHECK(jsonb_typeof(source_span)='object'),
            provenance_sha256 TEXT NOT NULL CHECK(length(provenance_sha256)=64),
            scope TEXT NOT NULL,
            locale TEXT NOT NULL,
            product TEXT,
            subject_user_id TEXT,
            subject_conversation_id TEXT,
            deletion_epoch BIGINT NOT NULL CHECK(deletion_epoch >= 0),
            embedding vector,
            lexical_document TEXT NOT NULL,
            search_tsv TSVECTOR NOT NULL,
            projected_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY (corpus, backend_id, generation_id)
                REFERENCES retrieval.retrieval_generation_registry
                (corpus, backend_id, generation_id),
            UNIQUE (tenant_id, backend_id, generation_id, source_id,
                    source_revision, source_checksum, source_span),
            CHECK((subject_user_id IS NULL) = (subject_conversation_id IS NULL))
        )
    """)
    op.execute("""
        CREATE TABLE retrieval.service_episode_search (
            candidate_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            corpus TEXT GENERATED ALWAYS AS ('SERVICE_EPISODE') STORED,
            user_id TEXT NOT NULL,
            entity_ids TEXT[] NOT NULL DEFAULT '{}',
            source_conversation_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            episode_id TEXT NOT NULL,
            episode_revision TEXT NOT NULL,
            outcome_receipt_ref TEXT NOT NULL,
            provenance_sha256 TEXT NOT NULL CHECK(length(provenance_sha256)=64),
            deletion_epoch BIGINT NOT NULL CHECK(deletion_epoch >= 0),
            verified_at TIMESTAMPTZ NOT NULL,
            embedding vector,
            lexical_document TEXT NOT NULL,
            search_tsv TSVECTOR NOT NULL,
            projected_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY (corpus, backend_id, generation_id)
                REFERENCES retrieval.retrieval_generation_registry
                (corpus, backend_id, generation_id),
            UNIQUE (tenant_id, backend_id, generation_id,
                    episode_id, episode_revision)
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_search_projection()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, retrieval, dialogpilot_app, public AS $$
        DECLARE
            expected_corpus TEXT;
            expected_dimension INTEGER;
            generation_state TEXT;
            tombstoned_at TIMESTAMPTZ;
            current_epoch BIGINT;
            scoped_user TEXT;
            scoped_conversation TEXT;
        BEGIN
            expected_corpus := CASE TG_TABLE_NAME
                WHEN 'knowledge_chunk_search' THEN 'KNOWLEDGE'
                WHEN 'service_episode_search' THEN 'SERVICE_EPISODE'
                ELSE NULL
            END;
            SELECT embedding_dimension, state
              INTO expected_dimension, generation_state
              FROM retrieval.retrieval_generation_registry
             WHERE corpus=expected_corpus AND backend_id=NEW.backend_id
               AND generation_id=NEW.generation_id;
            IF NOT FOUND OR generation_state NOT IN ('BUILDING', 'READY', 'ACTIVE') THEN
                RAISE EXCEPTION 'retrieval generation is not writable'
                    USING ERRCODE='55000';
            END IF;
            IF NEW.embedding IS NOT NULL AND public.vector_dims(NEW.embedding) <> expected_dimension THEN
                RAISE EXCEPTION 'retrieval embedding dimension mismatch'
                    USING ERRCODE='22000';
            END IF;
            IF TG_TABLE_NAME = 'knowledge_chunk_search' THEN
                scoped_user := NEW.subject_user_id;
                scoped_conversation := NEW.subject_conversation_id;
            ELSE
                scoped_user := NEW.user_id;
                scoped_conversation := NEW.source_conversation_id;
            END IF;
            IF scoped_conversation IS NOT NULL THEN
                SELECT deleted_at, deletion_epoch INTO tombstoned_at, current_epoch
                  FROM dialogpilot_app.conversations
                 WHERE tenant_id=NEW.tenant_id AND user_id=scoped_user
                   AND conversation_id=scoped_conversation
                 FOR SHARE;
                IF NOT FOUND OR tombstoned_at IS NOT NULL
                   OR current_epoch <> NEW.deletion_epoch THEN
                    RAISE EXCEPTION 'retrieval subject is deletion-fenced'
                        USING ERRCODE='55000';
                END IF;
            ELSIF NEW.deletion_epoch <> 0 THEN
                RAISE EXCEPTION 'public retrieval row must use deletion epoch zero'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    for table in ("knowledge_chunk_search", "service_episode_search"):
        op.execute(f"""
            CREATE TRIGGER {table}_write_fence
            BEFORE INSERT OR UPDATE ON retrieval.{table}
            FOR EACH ROW EXECUTE FUNCTION retrieval.guard_search_projection()
        """)
        op.execute(f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE ON retrieval.{table}
            FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
        """)
        op.execute(f"""
            CREATE INDEX {table}_search_tsv_gin
            ON retrieval.{table} USING GIN (search_tsv)
        """)
        op.execute(f"""
            CREATE INDEX {table}_generation_filter_idx
            ON retrieval.{table} (tenant_id, backend_id, generation_id)
        """)
    op.execute("""
        CREATE INDEX knowledge_chunk_search_acl_idx
        ON retrieval.knowledge_chunk_search
        (tenant_id, scope, locale, product, backend_id, generation_id)
    """)
    op.execute("""
        CREATE INDEX service_episode_search_acl_idx
        ON retrieval.service_episode_search
        (tenant_id, user_id, backend_id, generation_id, verified_at DESC)
    """)
    op.execute("GRANT USAGE ON SCHEMA retrieval TO dialogpilot_retrieval")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA retrieval TO dialogpilot_retrieval")
    op.execute("""
        GRANT INSERT, DELETE ON
            retrieval.knowledge_chunk_search,
            retrieval.service_episode_search
        TO dialogpilot_retrieval
    """)


def downgrade() -> None:
    raise RuntimeError("retrieval foundation downgrade requires forward-fix")
