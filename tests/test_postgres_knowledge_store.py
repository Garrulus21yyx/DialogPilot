"""Direct local Knowledge ingestion has one PostgreSQL active generation."""
from __future__ import annotations

import pytest

from application.hybrid_retrieval import RetrievalCorpus
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.retrieval_postgres import PostgresRetrievalGenerationRegistry
from mcp.source_document import SourceDocument


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

    def embed(texts):
        return [[1.0] + [0.0] * 383 for _text in texts]

    value = PostgresKnowledgeStore(
        pool, tenant_id="tenant-a", embedding_function=embed,
    )
    try:
        yield value, pool
    finally:
        pool.close()


def _document(source_id: str, content: str) -> SourceDocument:
    return SourceDocument.create(
        source_id=source_id, title=source_id, content=content,
    )


def test_direct_ingest_replaces_the_single_active_generation(store):
    knowledge, pool = store
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
    knowledge, pool = store
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
