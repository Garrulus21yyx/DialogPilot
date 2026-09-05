"""Knowledge ingestion budget policy; online Agent limits live in the framework."""
from core.cost_budget import OfflineIngestBudget


OFFLINE_KNOWLEDGE_INGEST_BUDGET = OfflineIngestBudget(
    max_sources_per_batch=256,
    max_source_bytes=10 * 1024 * 1024,
    max_total_source_bytes=10 * 1024 * 1024,
    max_chunks_per_batch=4096,
    max_embedding_tokens_per_batch=2_000_000,
    policy_version="offline-knowledge-ingest-budget-v1",
)
