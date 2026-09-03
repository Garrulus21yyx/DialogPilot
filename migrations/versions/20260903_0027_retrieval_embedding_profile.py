"""Pin provider and document/query preprocessing in retrieval generations.

Revision ID: 20260903_0027
Revises: 20260902_0026
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0027"
down_revision: Union[str, Sequence[str], None] = "20260902_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids = (
    "location:knowledge-index:v1",
    "location:episode-index:v1",
)


def upgrade() -> None:
    # Existing generations retain an explicit legacy identity.  They are not
    # relabelled as a model-backed generation; current producers create a new
    # immutable generation with a complete profile.
    op.execute("""
        ALTER TABLE retrieval.retrieval_generation_registry
            ADD COLUMN embedding_provider TEXT NOT NULL
                DEFAULT 'legacy-unrecorded',
            ADD COLUMN embedding_provider_kind TEXT NOT NULL
                DEFAULT 'LEGACY_UNSPECIFIED'
                CHECK(embedding_provider_kind IN (
                    'MODEL', 'HASH_BASELINE', 'LEGACY_UNSPECIFIED'
                )),
            ADD COLUMN embedding_model_version TEXT NOT NULL
                DEFAULT 'legacy-unrecorded',
            ADD COLUMN embedding_document_preprocessing TEXT NOT NULL
                DEFAULT 'legacy-unrecorded',
            ADD COLUMN embedding_query_preprocessing TEXT NOT NULL
                DEFAULT 'legacy-unrecorded'
    """)
    op.execute("""
        ALTER TABLE retrieval.retrieval_generation_registry
            DROP COLUMN replayable_across_environments,
            ADD COLUMN replayable_across_environments BOOLEAN GENERATED ALWAYS AS (
                embedding_provider_kind != 'LEGACY_UNSPECIFIED'
                AND length(embedding_model_digest)=64
            ) STORED
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_generation_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(OLD.corpus, OLD.backend_id, OLD.generation_id,
                   OLD.backend_fingerprint, OLD.immutable_fingerprint,
                   OLD.schema_version, OLD.source_watermark,
                   OLD.embedding_provider, OLD.embedding_provider_kind,
                   OLD.embedding_model, OLD.embedding_model_version,
                   OLD.embedding_dimension, OLD.embedding_model_digest,
                   OLD.embedding_document_preprocessing,
                   OLD.embedding_query_preprocessing,
                   OLD.distance_metric, OLD.vector_extension_version,
                   OLD.index_method, OLD.index_params, OLD.chinese_tokenizer,
                   OLD.lexical_ranker, OLD.manifest_hash)
               IS DISTINCT FROM
               ROW(NEW.corpus, NEW.backend_id, NEW.generation_id,
                   NEW.backend_fingerprint, NEW.immutable_fingerprint,
                   NEW.schema_version, NEW.source_watermark,
                   NEW.embedding_provider, NEW.embedding_provider_kind,
                   NEW.embedding_model, NEW.embedding_model_version,
                   NEW.embedding_dimension, NEW.embedding_model_digest,
                   NEW.embedding_document_preprocessing,
                   NEW.embedding_query_preprocessing,
                   NEW.distance_metric, NEW.vector_extension_version,
                   NEW.index_method, NEW.index_params, NEW.chinese_tokenizer,
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


def downgrade() -> None:
    raise RuntimeError("Retrieval embedding profiles use forward-only migrations")
