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


def _document(
    source_id: str, content: str, *, title: str | None = None,
) -> SourceDocument:
    return SourceDocument.create(
        source_id=source_id, title=title or source_id, content=content,
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


def test_title_change_revisions_contextual_retrieval_text(store):
    knowledge, pool, provider = store
    content = "退款三个工作日到账。"
    knowledge.add_documents((
        _document("refund", content, title="退款说明"),
    ))
    first = knowledge.active_generation()

    knowledge.add_documents((
        _document("refund", content, title="退款到账时间"),
    ))
    second = knowledge.active_generation()

    assert second.generation_id != first.generation_id
    assert any(text.startswith("[TITLE] 退款到账时间\n") for text in provider.document_inputs[-1])
    with pool.transaction() as connection:
        revisions = connection.execute("""
            SELECT title, revision_id
            FROM retrieval.knowledge_source_revisions
            WHERE tenant_id='tenant-a' AND source_id='refund'
            ORDER BY title
        """).fetchall()
    assert [row[0] for row in revisions] == ["退款到账时间", "退款说明"]
    assert len({row[1] for row in revisions}) == 2


def test_ingest_embeds_contextual_chunk_while_source_span_stays_authoritative(store):
    knowledge, pool, provider = store
    content = "退款ABC 将在三个工作日内到账。"
    knowledge.add_documents((_document("mixed", content),))
    generation = knowledge.active_generation()

    expected_retrieval_text = (
        "[TITLE] mixed\n[CONTENT] " + content
    )
    assert provider.document_inputs == [(expected_retrieval_text,)]
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
    assert lexical_document == postgres_lexical_document(expected_retrieval_text)
    assert lexical_document != expected_retrieval_text


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


def test_incremental_import_embeds_only_changed_source_spans(store):
    knowledge, _, provider = store
    first = knowledge.import_documents((_document('refund','退款七天。'),))
    second = knowledge.import_documents((_document('delivery','配送三天。'),))
    assert len(provider.document_inputs) == 2
    assert len(provider.document_inputs[1]) == 1
    assert '[TITLE] delivery' in provider.document_inputs[1][0]
    assert first.revisions[0].source_id == 'refund'
    assert second.revisions[0].source_id == 'delivery'
    third = knowledge.import_documents((_document('refund','退款三天。'),))
    assert len(provider.document_inputs[2]) == 1
    assert third.revisions[0].revision_id != first.revisions[0].revision_id
    # Each committed receipt remains the actual stored identity after later writes.
    assert any(item.revision_id == first.revisions[0].revision_id for item in knowledge._active_sources())


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_batch_budget_allows_cumulative_growth_and_idempotency(store, monkeypatch, limit):
    import infrastructure.postgres_knowledge_store as module
    monkeypatch.setattr(module, 'OFFLINE_KNOWLEDGE_INGEST_BUDGET', replace(
        module.OFFLINE_KNOWLEDGE_INGEST_BUDGET, max_chunks_per_batch=limit))
    knowledge, _, provider = store
    for batch in range(3):
        incoming = tuple(_document(f'b{batch}-{i}', f'规则{batch}项目{i}。') for i in range(limit))
        knowledge.import_documents(incoming)
    active = knowledge.active_generation().generation_id
    assert knowledge.doc_count() == 3 * limit
    assert all(len(values) <= limit for values in provider.document_inputs)
    calls = len(provider.document_inputs)
    knowledge.import_documents(incoming)
    assert knowledge.active_generation().generation_id == active
    assert len(provider.document_inputs) == calls
    # A revision after cumulative growth is also a one-source batch.
    knowledge.import_documents((_document('b0-0', '更新后的规则。'),))
    assert len(provider.document_inputs[-1]) == 1
    assert knowledge.doc_count() == 3 * limit + 1


def test_oversized_batch_preserves_active_generation(store, monkeypatch):
    import infrastructure.postgres_knowledge_store as module
    from core.cost_budget import OfflineIngestBudgetExceeded
    monkeypatch.setattr(module, 'OFFLINE_KNOWLEDGE_INGEST_BUDGET', replace(
        module.OFFLINE_KNOWLEDGE_INGEST_BUDGET, max_chunks_per_batch=1))
    knowledge, _, provider = store
    knowledge.import_documents((_document('initial', '初始规则。'),))
    active = knowledge.active_generation().generation_id
    calls = len(provider.document_inputs)
    with pytest.raises(OfflineIngestBudgetExceeded):
        knowledge.import_documents((_document('new1', '规则一。'), _document('new2', '规则二。')))
    assert knowledge.active_generation().generation_id == active
    assert knowledge.doc_count() == 1
    assert len(provider.document_inputs) == calls


def test_model_rebuild_is_batched_and_failure_does_not_activate(store, monkeypatch):
    import infrastructure.postgres_knowledge_store as module
    monkeypatch.setattr(module, 'OFFLINE_KNOWLEDGE_INGEST_BUDGET', replace(
        module.OFFLINE_KNOWLEDGE_INGEST_BUDGET, max_chunks_per_batch=1))
    knowledge, pool, old_provider = store
    for i in range(3):
        knowledge.import_documents((_document(str(i), f'规则{i}。'),))
    active = knowledge.active_generation().generation_id
    provider = RecordingEmbeddingProvider(replace(old_provider.profile, model_version='revision-2'))
    rebuilding = PostgresKnowledgeStore(pool, tenant_id='tenant-a', embedding_provider=provider)
    original = provider.embed_documents
    def fail_second(texts):
        if provider.document_inputs:
            raise RuntimeError('embedding failed during rebuild')
        return original(texts)
    monkeypatch.setattr(provider, 'embed_documents', fail_second)
    from infrastructure.knowledge_embedding import KnowledgeEmbeddingContractError
    with pytest.raises(KnowledgeEmbeddingContractError, match='embedding provider failed'):
        rebuilding.import_documents((_document('2', '规则2。'),))
    assert knowledge.active_generation().generation_id == active
    assert knowledge.doc_count() == 3
    monkeypatch.setattr(provider, 'embed_documents', original)
    provider.document_inputs.clear()
    rebuilding.import_documents((_document('2', '规则2。'),))
    assert [len(values) for values in provider.document_inputs] == [1, 1, 1]
    assert rebuilding.active_generation().generation_id != active
    assert rebuilding.doc_count() == 3
