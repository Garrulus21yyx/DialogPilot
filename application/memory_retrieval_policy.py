"""Versioned Memory retrieval fusion and rollout binding contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum


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


LEGACY_MEMORY_RETRIEVAL_POLICY = MemoryRetrievalPolicy()


class MemoryRetrievalConsumerMode(str, Enum):
    SHADOW = "SHADOW"
    PINNED_CANARY = "PINNED_CANARY"
    ACTIVE = "ACTIVE"
    LEGACY = "LEGACY"


@dataclass(frozen=True)
class MemoryRetrievalBinding:
    """One pinned tuple; policy/backend/corpus generations cannot drift apart."""

    mode: MemoryRetrievalConsumerMode
    policy_fingerprint: str
    backend_id: str
    backend_generation: str
    corpus_generation: str
    previous_policy_fingerprint: str
    previous_backend_id: str
    previous_backend_generation: str
    previous_corpus_generation: str
    version: int

    def __post_init__(self) -> None:
        required = (
            self.policy_fingerprint, self.backend_id, self.backend_generation,
            self.corpus_generation, self.previous_policy_fingerprint,
            self.previous_backend_id, self.previous_backend_generation,
            self.previous_corpus_generation,
        )
        if any(not value.strip() for value in required) or self.version < 1:
            raise ValueError("memory retrieval binding is incomplete")

    def rollback(self) -> "MemoryRetrievalBinding":
        return MemoryRetrievalBinding(
            mode=MemoryRetrievalConsumerMode.LEGACY,
            policy_fingerprint=self.previous_policy_fingerprint,
            backend_id=self.previous_backend_id,
            backend_generation=self.previous_backend_generation,
            corpus_generation=self.previous_corpus_generation,
            previous_policy_fingerprint=self.previous_policy_fingerprint,
            previous_backend_id=self.previous_backend_id,
            previous_backend_generation=self.previous_backend_generation,
            previous_corpus_generation=self.previous_corpus_generation,
            version=self.version + 1,
        )
