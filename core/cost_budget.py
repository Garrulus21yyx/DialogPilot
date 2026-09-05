"""Offline knowledge ingestion resource limits and typed exhaustion."""
from dataclasses import dataclass


@dataclass(frozen=True)
class OfflineIngestBudget:
    max_sources_per_batch: int
    max_source_bytes: int
    max_total_source_bytes: int
    max_chunks_per_batch: int
    max_embedding_tokens_per_batch: int
    policy_version: str

    def __post_init__(self) -> None:
        if min(
            self.max_sources_per_batch,
            self.max_source_bytes,
            self.max_total_source_bytes,
            self.max_chunks_per_batch,
            self.max_embedding_tokens_per_batch,
        ) < 1 or not self.policy_version:
            raise ValueError("offline ingest budget is invalid")


class OfflineIngestBudgetExceeded(RuntimeError):
    def __init__(self, *, dimension: str, observed: int, limit: int, policy_version: str):
        self.code = "OFFLINE_INGEST_BUDGET_EXHAUSTED"
        self.dimension = dimension
        self.observed = int(observed)
        self.limit = int(limit)
        self.policy_version = policy_version
        super().__init__(f"offline ingest {dimension}={observed} exceeds {limit}")
