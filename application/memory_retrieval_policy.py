"""Versioned ServiceEpisode retrieval fusion policy."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MemoryRetrievalPolicy:
    version: str = "memory-retrieval-policy-v1"
    vector_weight: float = 0.30
    lexical_weight: float = 0.60
    recency_weight: float = 0.10
    rrf_k: int = 60
    lexical_pool: int = 20

    def __post_init__(self) -> None:
        weights = (self.vector_weight, self.lexical_weight, self.recency_weight)
        if any(value < 0 for value in weights) or sum(weights) <= 0:
            raise ValueError("memory retrieval weights are invalid")
        if self.rrf_k < 1 or self.lexical_pool < 1:
            raise ValueError("memory retrieval bounds are invalid")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()


DEFAULT_MEMORY_RETRIEVAL_POLICY = MemoryRetrievalPolicy()
