"""Versioned Memory retrieval fusion and rollout binding contracts."""
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


LEGACY_MEMORY_RETRIEVAL_POLICY = MemoryRetrievalPolicy()


@dataclass(frozen=True)
class MemoryRetrievalTarget:
    policy_fingerprint: str
    backend_id: str
    backend_generation: str
    corpus_generation: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (
            self.policy_fingerprint, self.backend_id, self.backend_generation,
            self.corpus_generation,
        )):
            raise ValueError("memory retrieval target is incomplete")
        if len(self.policy_fingerprint) != 64:
            raise ValueError("memory retrieval policy fingerprint must be SHA-256")


@dataclass(frozen=True)
class MemoryRetrievalBinding:
    """The only ServiceEpisode target; disabled until direct-cutover acceptance."""

    target: MemoryRetrievalTarget
    enabled: bool
    version: int

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("memory retrieval binding is incomplete")

    def activate(self) -> "MemoryRetrievalBinding":
        if self.enabled:
            raise ValueError("memory retrieval binding is already enabled")
        return MemoryRetrievalBinding(self.target, True, self.version + 1)
