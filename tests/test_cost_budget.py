"""Offline ingest limits reject oversized sources before persistence."""
import pytest

from core.cost_budget import OfflineIngestBudget, OfflineIngestBudgetExceeded
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from mcp.source_document import SourceDocument


def test_offline_ingest_budget_is_independent_and_blocks_before_persistence(
    monkeypatch,
):
    budget = OfflineIngestBudget(
        max_sources_per_batch=2, max_source_bytes=8, max_total_source_bytes=12,
        max_chunks_per_batch=10, max_embedding_tokens_per_batch=100,
        policy_version="offline-test-v1",
    )
    monkeypatch.setattr(
        "infrastructure.postgres_knowledge_store.OFFLINE_KNOWLEDGE_INGEST_BUDGET",
        budget,
    )

    with pytest.raises(OfflineIngestBudgetExceeded) as raised:
        PostgresKnowledgeStore._validate_budget((SourceDocument.create(
            source_id="too-large", title="large", content="0123456789",
        ),))

    assert raised.value.code == "OFFLINE_INGEST_BUDGET_EXHAUSTED"
    assert raised.value.dimension == "source_bytes"
    with pytest.raises(OfflineIngestBudgetExceeded) as batch_raised:
        PostgresKnowledgeStore._validate_budget(tuple(
            SourceDocument.create(source_id=value, title=value, content=content)
            for value, content in (("one", "1234567"), ("two", "7654321"))
        ))
    assert batch_raised.value.dimension == "total_source_bytes"
