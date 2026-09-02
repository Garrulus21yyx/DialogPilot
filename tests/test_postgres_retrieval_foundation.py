"""M2-PF01 PostgreSQL generation, role, dimension and deletion fences."""
import psycopg
import pytest

from application.hybrid_retrieval import (
    DistanceMetric,
    GenerationConflict,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
)
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.retrieval_postgres import (
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)


SHA = "b" * 64


def _generation(suffix="one", *, corpus=RetrievalCorpus.KNOWLEDGE):
    return RetrievalGeneration(
        generation_id=f"pg-generation-{suffix}",
        corpus=corpus,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint=f"pg-backend-{suffix}",
        schema_version="retrieval-v1",
        source_watermark="source-revision-10",
        embedding_model="all-MiniLM-L6-v2",
        embedding_dimension=384,
        embedding_model_digest="UNVERIFIED:legacy-model-weight-digest",
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer="ascii-cjk-unigram-bigram-v1",
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash=SHA,
    )


@pytest.fixture()
def retrieval_foundation(postgres_database_url):
    result = PostgresMigrationRunner(postgres_database_url).upgrade()
    platform = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
        statement_timeout_ms=900,
    ))
    platform.open()
    retrieval.open()
    with platform.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                retrieval.knowledge_chunk_search,
                retrieval.service_episode_search,
                retrieval.retrieval_generation_pointers,
                retrieval.retrieval_generation_registry
            CASCADE
        """)
    try:
        yield result, platform, retrieval
    finally:
        retrieval.close()
        platform.close()


def test_retrieval_pool_has_independent_role_budget_timeout_and_metrics(
    retrieval_foundation,
):
    result, _, retrieval = retrieval_foundation
    assert result["head"] == "20260902_0012"
    assert retrieval.config.max_size == 2
    with retrieval.transaction() as connection:
        row = connection.execute("""
            SELECT current_user, current_setting('statement_timeout'),
                   current_schemas(false), extversion
            FROM pg_extension WHERE extname='vector'
        """).fetchone()
    assert row == (
        "dialogpilot_retrieval", "900ms", ["retrieval", "public"], "0.8.6",
    )
    assert retrieval.metrics().transactions_started == 1
    assert retrieval.metrics().transactions_succeeded == 1


def test_generation_definition_is_immutable_and_pointer_activation_is_atomic(
    retrieval_foundation,
):
    _, platform, _ = retrieval_foundation
    registry = PostgresRetrievalGenerationRegistry(platform)
    first = registry.register(_generation("registry"))
    assert registry.register(first) == first
    with pytest.raises(GenerationConflict, match="immutable"):
        registry.register(RetrievalGeneration(**{
            **first.__dict__, "embedding_dimension": 768,
        }))
    registry.transition(first.generation_id, GenerationState.BUILDING)
    registry.transition(first.generation_id, GenerationState.READY)
    pointer = registry.activate(first.generation_id, expected_version=0)
    assert pointer.active_generation_id == first.generation_id
    assert pointer.version == 1
    with pytest.raises(GenerationConflict, match="only READY"):
        registry.activate(first.generation_id, expected_version=1)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable"):
        with platform.transaction() as connection:
            connection.execute("""
                UPDATE retrieval.retrieval_generation_registry
                SET embedding_dimension=768 WHERE generation_id=%s
            """, (first.generation_id,))


def test_search_projection_fails_closed_on_dimension_missing_subject_and_epoch(
    retrieval_foundation,
):
    _, platform, retrieval = retrieval_foundation
    registry = PostgresRetrievalGenerationRegistry(platform)
    knowledge = registry.register(_generation("knowledge-write"))
    registry.transition(knowledge.generation_id, GenerationState.BUILDING)

    with pytest.raises(psycopg.errors.DataException, match="dimension mismatch"):
        with retrieval.transaction() as connection:
            connection.execute("""
                INSERT INTO knowledge_chunk_search (
                    candidate_id, tenant_id, backend_id, generation_id,
                    source_id, source_revision, source_checksum, source_span,
                    provenance_sha256, scope, locale, deletion_epoch,
                    embedding, lexical_document, projected_at
                ) VALUES (
                    'bad-dimension', 'tenant-a', %s, %s,
                    'source-a', 'revision-a', %s, '{}'::jsonb,
                    %s, 'public', 'zh-CN', 0,
                    '[0.1,0.2]'::vector, '退款 流程', now()
                )
            """, (knowledge.backend_id, knowledge.generation_id, SHA, SHA))

    episode = registry.register(_generation(
        "episode-write", corpus=RetrievalCorpus.SERVICE_EPISODE,
    ))
    registry.transition(episode.generation_id, GenerationState.BUILDING)
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="deletion-fenced",
    ):
        with retrieval.transaction() as connection:
            connection.execute("""
                INSERT INTO service_episode_search (
                    candidate_id, tenant_id, user_id, source_conversation_id,
                    backend_id, generation_id, episode_id, episode_revision,
                    outcome_receipt_ref, provenance_sha256, deletion_epoch,
                    verified_at, lexical_document, projected_at
                ) VALUES (
                    'missing-subject', 'tenant-a', 'user-a', 'conversation-a',
                    %s, %s, 'episode-a', 'revision-a', 'receipt-a', %s, 0,
                    now(), '退款 结果', now()
                )
            """, (episode.backend_id, episode.generation_id, SHA))


def test_corpus_tables_are_separate_and_retrieval_role_cannot_mutate_registry(
    retrieval_foundation,
):
    _, _, retrieval = retrieval_foundation
    with retrieval.transaction() as connection:
        tables = connection.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='retrieval'
            ORDER BY table_name
        """).fetchall()
    assert ("knowledge_chunk_search",) in tables
    assert ("service_episode_search",) in tables
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with retrieval.transaction() as connection:
            connection.execute(
                "DELETE FROM retrieval_generation_registry WHERE false"
            )
