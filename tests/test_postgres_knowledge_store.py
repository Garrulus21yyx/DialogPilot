"""Direct local Knowledge ingestion has one PostgreSQL active generation."""
from __future__ import annotations

from dataclasses import replace

import pytest

from application.chinese_lexical import postgres_lexical_document
from application.hybrid_retrieval import (
    EmbeddingProfile,
    EmbeddingProviderKind,
    RetrievalCorpus,
)
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.retrieval_postgres import PostgresRetrievalGenerationRegistry
from mcp.source_document import SourceDocument


class RecordingEmbeddingProvider:
    def __init__(self, profile: EmbeddingProfile | None = None):
        self.profile = profile or EmbeddingProfile(
            provider="test-model-provider",
            provider_kind=EmbeddingProviderKind.MODEL,
            model="test-multilingual-bi-encoder",
            model_version="revision-1",
            dimension=384,
            model_digest="e" * 64,
            document_preprocessing="raw-document-test-v1",
            query_preprocessing="raw-query-test-v1",
        )
        self.document_inputs: list[tuple[str, ...]] = []
        self.query_inputs: list[tuple[str, ...]] = []

    def embed_documents(self, raw_source_chunks):
        self.document_inputs.append(tuple(raw_source_chunks))
        return [[1.0] + [0.0] * 383 for _text in raw_source_chunks]

    def embed_queries(self, raw_queries):
        self.query_inputs.append(tuple(raw_queries))
        return [[1.0] + [0.0] * 383 for _text in raw_queries]


@pytest.fixture()
def store(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=3,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                retrieval.canonical_projection_receipts,
                retrieval.canonical_projection_outbox,
                retrieval.knowledge_source_chunk_specs,
                retrieval.knowledge_source_manifest_entries,
                retrieval.knowledge_source_manifests,
                retrieval.knowledge_source_revisions,
                retrieval.knowledge_chunk_search,
                retrieval.retrieval_generation_registry
            CASCADE
        """)

    provider = RecordingEmbeddingProvider()
    value = PostgresKnowledgeStore(
        pool, tenant_id="tenant-a", embedding_provider=provider,
    )
    try:
        yield value, pool, provider
    finally:
        pool.close()


def _document(source_id: str, content: str) -> SourceDocument:
    return SourceDocument.create(
        source_id=source_id, title=source_id, content=content,
    )


def test_direct_ingest_replaces_the_single_active_generation(store):
    knowledge, pool, _ = store
    assert knowledge.add_documents((_document("refund", "退款三个工作日到账。"),)) == 1
    first = knowledge.active_generation()
    assert knowledge.doc_count() == 1

    assert knowledge.add_documents((_document("delivery", "配送需要三天。"),)) == 2
    second = knowledge.active_generation()
    assert second.generation_id != first.generation_id
    assert PostgresRetrievalGenerationRegistry(pool).active(
        RetrievalCorpus.KNOWLEDGE, backend_id=knowledge.backend_id,
    ).generation_id == second.generation_id
    with pool.transaction() as connection:
        states = connection.execute("""
            SELECT state, count(*)
            FROM retrieval.retrieval_generation_registry
            WHERE corpus='KNOWLEDGE' AND backend_id=%s
            GROUP BY state ORDER BY state
        """, (knowledge.backend_id,)).fetchall()
    assert states == [("ACTIVE", 1), ("RETIRED", 1)]
    assert knowledge.doc_count() == 2


def test_identical_ingest_is_idempotent(store):
    knowledge, pool, _ = store
    document = _document("refund", "退款三个工作日到账。")
    knowledge.add_documents((document,))
    generation_id = knowledge.active_generation().generation_id
    knowledge.add_documents((document,))
    assert knowledge.active_generation().generation_id == generation_id
    with pool.transaction() as connection:
        assert connection.execute("""
            SELECT count(*) FROM retrieval.retrieval_generation_registry
            WHERE corpus='KNOWLEDGE'
        """).fetchone()[0] == 1


def test_ingest_embeds_raw_chunk_while_fts_keeps_lexical_projection(store):
    knowledge, pool, provider = store
    content = "退款ABC 将在三个工作日内到账。"
    knowledge.add_documents((_document("mixed", content),))
    generation = knowledge.active_generation()

    assert provider.document_inputs == [(content,)]
    assert generation.embedding_profile == provider.profile
    assert generation.embedding_metadata_complete is True
    query = "退款ABC 到账了吗？"
    knowledge.embed_query(query, generation)
    assert provider.query_inputs == [(query,)]

    with pool.transaction() as connection:
        lexical_document = connection.execute("""
            SELECT lexical_document
            FROM retrieval.knowledge_source_chunk_specs
            WHERE generation_id=%s
        """, (generation.generation_id,)).fetchone()[0]
    assert lexical_document == postgres_lexical_document(content)
    assert lexical_document != content


def test_preprocessing_change_builds_new_immutable_generation(store):
    knowledge, pool, provider = store
    document = _document("refund", "退款三个工作日到账。")
    knowledge.add_documents((document,))
    first = knowledge.active_generation()

    changed_profile = replace(
        provider.profile, document_preprocessing="raw-document-test-v2",
    )
    changed_store = PostgresKnowledgeStore(
        pool,
        tenant_id="tenant-a",
        embedding_provider=RecordingEmbeddingProvider(changed_profile),
    )
    changed_store.add_documents((document,))
    second = changed_store.active_generation()

    assert second.generation_id != first.generation_id
    assert second.embedding_document_preprocessing == "raw-document-test-v2"
    assert first.immutable_fingerprint() != second.immutable_fingerprint()
    with pool.transaction() as connection:
        states = dict(connection.execute("""
            SELECT generation_id, state
            FROM retrieval.retrieval_generation_registry
            WHERE corpus='KNOWLEDGE'
        """).fetchall())
    assert states[first.generation_id] == "RETIRED"
    assert states[second.generation_id] == "ACTIVE"


def test_chunk_strategy_change_builds_a_distinct_generation(store):
    knowledge, pool, provider = store
    document = _document("refund", "退款三个工作日到账。")
    knowledge.add_documents((document,))
    first = knowledge.active_generation()

    fixed_store = PostgresKnowledgeStore(
        pool,
        tenant_id="tenant-a",
        chunk_strategy="fixed_tokens",
        embedding_provider=provider,
    )
    fixed_store.add_documents((document,))
    second = fixed_store.active_generation()

    assert second.generation_id != first.generation_id
    assert fixed_store.index_manifest["chunk_strategy"] == "fixed_tokens"
    assert fixed_store.index_manifest["chunk_max_tokens"] == 512
    assert fixed_store.index_manifest["chunk_overlap_tokens"] == 64


def test_default_provider_is_explicitly_a_hash_baseline():
    knowledge = PostgresKnowledgeStore(object(), tenant_id="tenant-baseline")
    assert knowledge.embedding_profile.provider_kind is (
        EmbeddingProviderKind.HASH_BASELINE
    )
    assert knowledge.embedding_profile.is_baseline is True
