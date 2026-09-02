"""M2-PF01 conformance for the PostgreSQL candidate backend."""
from __future__ import annotations

from dataclasses import replace

import pytest

from application.chinese_lexical import (
    TOKENIZER_VERSION,
    postgres_lexical_document,
    postgres_websearch_or_query,
    tokenize_ascii_cjk_unigram_bigram,
)
from application.hybrid_retrieval import (
    DistanceMetric,
    EpisodeSearchScope,
    GenerationState,
    HybridRetrievalRequest,
    KnowledgeSearchScope,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from infrastructure.hybrid_retrieval_backend import (
    PostgresHybridBackend,
    create_generation_hnsw_index,
    hnsw_index_name,
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
from memory.hybrid_retrieval import HybridMemoryRetriever


SHA = "c" * 64


def _vector(index: int) -> str:
    values = [0.0] * 384
    values[index] = 1.0
    return "[" + ",".join(map(str, values)) + "]"


def _generation(suffix="one", *, corpus=RetrievalCorpus.KNOWLEDGE):
    return RetrievalGeneration(
        generation_id=f"backend-generation-{suffix}",
        corpus=corpus,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint=f"backend-fingerprint-{suffix}",
        schema_version="retrieval-v1",
        source_watermark="revision-2",
        embedding_model="all-MiniLM-L6-v2",
        embedding_dimension=384,
        embedding_model_digest="UNVERIFIED:legacy-model-weight-digest",
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer=TOKENIZER_VERSION,
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash=SHA,
    )


def _knowledge_request(generation, *, tenant="tenant-a", exact=False):
    return HybridRetrievalRequest(
        tenant_id=tenant,
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_fingerprint=generation.backend_fingerprint,
        generation_id=generation.generation_id,
        policy_fingerprint="knowledge-policy-v1",
        query_text="退款",
        query_embedding=tuple(float(item) for item in _vector(0)[1:-1].split(",")),
        scope=KnowledgeSearchScope("public", "zh-CN", "payments"),
        dense_limit=10,
        lexical_limit=10,
        exact_dense=exact,
    )


@pytest.fixture()
def backend_foundation(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    platform = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
        statement_timeout_ms=2000,
    ))
    platform.open()
    retrieval.open()
    with platform.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                retrieval.knowledge_chunk_search,
                retrieval.service_episode_search,
                retrieval.retrieval_generation_registry
            CASCADE
        """)
    try:
        yield platform, retrieval
    finally:
        retrieval.close()
        platform.close()


def test_frozen_tokenizer_is_shared_with_legacy_and_safe_for_bound_pg_query():
    text = "ORD-123 退款ABC v1.2 #Tag"
    expected = (
        "ord-123", "abc", "v1.2", "#tag", "退", "款", "退款",
    )
    assert tokenize_ascii_cjk_unigram_bigram(text) == expected
    assert tuple(HybridMemoryRetriever.tokenize(text)) == expected
    assert postgres_lexical_document(text) == " ".join(expected)
    assert postgres_websearch_or_query("退款") == "退 OR 款 OR 退款"


def test_postgres_dense_exact_ann_and_pg_fts_are_stable_and_acl_scoped(
    backend_foundation,
):
    platform, retrieval = backend_foundation
    registry = PostgresRetrievalGenerationRegistry(platform)
    generation = registry.register(_generation("knowledge"))
    registry.transition(generation.generation_id, GenerationState.BUILDING)
    rows = (
        ("candidate-refund", "tenant-a", "refund-source", "退款 流程", _vector(0)),
        ("candidate-logistics", "tenant-a", "logistics-source", "物流 查询", _vector(1)),
        ("candidate-other-tenant", "tenant-b", "other-source", "退款", _vector(0)),
    )
    with platform.transaction() as connection:
        for candidate_id, tenant_id, source_id, text, embedding in rows:
            connection.execute("""
                INSERT INTO retrieval.knowledge_chunk_search (
                    candidate_id, tenant_id, backend_id, generation_id,
                    source_id, source_revision, source_checksum, source_span,
                    provenance_sha256, scope, locale, product, deletion_epoch,
                    embedding, lexical_document, projected_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'revision-1', %s,
                    '{"start":0,"end":2}'::jsonb, %s,
                    'public', 'zh-CN', 'payments', 0,
                    %s::vector, %s, now()
                )
            """, (
                candidate_id, tenant_id, generation.backend_id,
                generation.generation_id, source_id, SHA, SHA,
                embedding, postgres_lexical_document(text),
            ))
    with platform.transaction() as connection:
        assert create_generation_hnsw_index(connection, generation) == (
            hnsw_index_name(generation)
        )
        assert create_generation_hnsw_index(connection, generation) == (
            hnsw_index_name(generation)
        )
    registry.transition(generation.generation_id, GenerationState.READY)
    registry.activate_direct(generation.generation_id)

    backend = PostgresHybridBackend(retrieval)
    ann = backend.retrieve(_knowledge_request(generation))
    exact = backend.retrieve(_knowledge_request(generation, exact=True))
    assert ann.status is RetrievalStatus.OK
    assert exact.status is RetrievalStatus.OK
    assert [item.candidate_id for item in ann.dense_candidates] == [
        item.candidate_id for item in exact.dense_candidates
    ]
    assert ann.dense_candidates[0].candidate_id == "candidate-refund"
    assert [item.candidate_id for item in ann.lexical_candidates] == [
        "candidate-refund",
    ]
    assert all(item.candidate_id != "candidate-other-tenant" for item in (
        *ann.dense_candidates, *ann.lexical_candidates,
    ))
    assert [item.rank for item in ann.dense_candidates] == list(
        range(1, len(ann.dense_candidates) + 1)
    )


def test_postgres_backend_fail_closed_status_algebra(backend_foundation):
    platform, retrieval = backend_foundation
    registry = PostgresRetrievalGenerationRegistry(platform)
    generation = registry.register(_generation("status"))
    backend = PostgresHybridBackend(retrieval)
    request = _knowledge_request(generation)
    assert backend.retrieve(request).status is RetrievalStatus.INVALID_CONTRACT
    registry.transition(generation.generation_id, GenerationState.BUILDING)
    registry.transition(generation.generation_id, GenerationState.READY)
    assert backend.retrieve(replace(
        request, backend_fingerprint="different",
    )).status is RetrievalStatus.CONFLICT
    assert backend.retrieve(replace(
        request, query_embedding=(0.1, 0.2),
    )).detail_code == "QUERY_EMBEDDING_DIMENSION_MISMATCH"
    no_match = backend.retrieve(replace(
        request, tenant_id="tenant-with-no-corpus",
    ))
    assert no_match.status is RetrievalStatus.NO_EVIDENCE


def test_episode_search_is_cross_user_isolated_and_never_cross_ranks_knowledge(
    backend_foundation,
):
    platform, retrieval = backend_foundation
    registry = PostgresRetrievalGenerationRegistry(platform)
    episode = registry.register(_generation(
        "episode", corpus=RetrievalCorpus.SERVICE_EPISODE,
    ))
    registry.transition(episode.generation_id, GenerationState.BUILDING)
    with platform.transaction() as connection:
        for user_id in ("user-a", "user-b"):
            connection.execute("""
                INSERT INTO dialogpilot_app.conversations (
                    tenant_id, user_id, conversation_id
                ) VALUES ('tenant-a', %s, %s)
            """, (user_id, f"conversation-{user_id}"))
        connection.execute("""
            INSERT INTO dialogpilot_app.conversations (
                tenant_id, user_id, conversation_id
            ) VALUES ('tenant-b', 'user-a', 'conversation-other-tenant')
        """)
    with platform.transaction() as connection:
        for user_id in ("user-a", "user-b"):
            connection.execute("""
                INSERT INTO retrieval.service_episode_search (
                    candidate_id, tenant_id, user_id, entity_ids,
                    source_conversation_id, backend_id, generation_id,
                    episode_id, episode_revision, outcome_receipt_ref,
                    provenance_sha256, deletion_epoch, verified_at,
                    lexical_document, projected_at
                ) VALUES (
                    %s, 'tenant-a', %s, ARRAY['order-1'], %s,
                    %s, %s, %s, 'revision-1', 'receipt-1', %s, 0,
                    now(), %s, now()
                )
            """, (
                f"candidate-{user_id}", user_id, f"conversation-{user_id}",
                episode.backend_id, episode.generation_id, f"episode-{user_id}",
                SHA, postgres_lexical_document("退款 成功"),
            ))
        connection.execute("""
            INSERT INTO retrieval.service_episode_search (
                candidate_id, tenant_id, user_id, entity_ids,
                source_conversation_id, backend_id, generation_id,
                episode_id, episode_revision, outcome_receipt_ref,
                provenance_sha256, deletion_epoch, verified_at,
                lexical_document, projected_at
            ) VALUES (
                'candidate-other-tenant', 'tenant-b', 'user-a',
                ARRAY['order-1'], 'conversation-other-tenant', %s, %s,
                'episode-other-tenant', 'revision-1', 'receipt-1', %s, 0,
                now(), %s, now()
            )
        """, (
            episode.backend_id, episode.generation_id, SHA,
            postgres_lexical_document("退款 成功"),
        ))
    registry.transition(episode.generation_id, GenerationState.READY)
    request = HybridRetrievalRequest(
        tenant_id="tenant-a", corpus=RetrievalCorpus.SERVICE_EPISODE,
        backend_fingerprint=episode.backend_fingerprint,
        generation_id=episode.generation_id,
        policy_fingerprint="episode-policy-v1", query_text="退款",
        query_embedding=None,
        scope=EpisodeSearchScope("user-a", ("order-1",)),
    )
    result = PostgresHybridBackend(retrieval).retrieve(request)
    assert result.status is RetrievalStatus.OK
    assert [item.candidate_id for item in result.lexical_candidates] == [
        "candidate-user-a",
    ]
    assert result.lexical_candidates[0].corpus is RetrievalCorpus.SERVICE_EPISODE
    assert result.lexical_candidates[0].freshness_at
    assert result.index_watermark == episode.source_watermark


def test_episode_scope_type_remains_separate_from_knowledge():
    generation = _generation(
        "episode-contract", corpus=RetrievalCorpus.SERVICE_EPISODE,
    )
    request = HybridRetrievalRequest(
        tenant_id="tenant-a", corpus=RetrievalCorpus.SERVICE_EPISODE,
        backend_fingerprint=generation.backend_fingerprint,
        generation_id=generation.generation_id,
        policy_fingerprint="episode-policy-v1", query_text="退款成功",
        query_embedding=None,
        scope=EpisodeSearchScope("user-a", ("order-1",)),
    )
    assert request.scope.user_id == "user-a"
