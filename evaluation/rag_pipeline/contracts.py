"""Stable contracts for stage-attributed RAG experiments."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Sequence, Tuple

from mcp.document_chunker import ChunkStrategy


@dataclass(frozen=True)
class RagDocument:
    document_id: str
    title: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id is required")
        if not self.content.strip():
            raise ValueError("document content is required")


@dataclass(frozen=True)
class EvidenceSpan:
    """Authoritative evidence in source-document coordinates."""

    document_id: str
    start_char: int
    end_char: int
    quote: str = ""
    relevance: int = 3

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("evidence document_id is required")
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise ValueError("evidence span must be a non-empty half-open interval")
        if self.relevance < 0:
            raise ValueError("evidence relevance must be non-negative")


@dataclass(frozen=True)
class RagCase:
    case_id: str
    group_id: str
    split: str
    query: str
    history: Tuple[str, ...] = ()
    evidence: Tuple[EvidenceSpan, ...] = ()
    query_types: Tuple[str, ...] = ()
    required_claims: Tuple[str, ...] = ()
    forbidden_claims: Tuple[str, ...] = ()
    answerable: bool = True

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.group_id.strip():
            raise ValueError("case_id and group_id are required")
        if self.split not in {"dev", "heldout", "stress"}:
            raise ValueError("split must be dev, heldout, or stress")
        if not self.query.strip():
            raise ValueError("query is required")
        if self.answerable and not self.evidence:
            raise ValueError("answerable cases require source evidence")


@dataclass(frozen=True)
class QueryVariant:
    kind: str
    text: str
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.text.strip():
            raise ValueError("query variant kind and text are required")
        if self.weight <= 0:
            raise ValueError("query variant weight must be positive")


@dataclass(frozen=True)
class PipelineConfig:
    """The bounded experiment surface; values are evidence-selected on Dev."""

    chunk_strategy: str = ChunkStrategy.STRUCTURE_AWARE.value
    chunk_max_tokens: int = 384
    chunk_overlap_tokens: int = 48
    vector_weight: float = 0.5
    lexical_weight: float = 0.5
    rrf_k: int = 30
    candidate_k: int = 20
    final_k: int = 5
    query_strategy: str = "raw"
    reranker: str = "none"
    context_max_tokens: int = 1800

    def __post_init__(self) -> None:
        ChunkStrategy(self.chunk_strategy)
        if self.chunk_max_tokens < 1:
            raise ValueError("chunk_max_tokens must be positive")
        if not 0 <= self.chunk_overlap_tokens < self.chunk_max_tokens:
            raise ValueError("chunk overlap must be smaller than chunk size")
        if self.vector_weight < 0 or self.lexical_weight < 0:
            raise ValueError("retrieval weights must be non-negative")
        if self.vector_weight + self.lexical_weight <= 0:
            raise ValueError("at least one retrieval weight must be positive")
        if self.rrf_k < 1 or self.candidate_k < 1 or self.final_k < 1:
            raise ValueError("rrf_k and retrieval cutoffs must be positive")
        if self.final_k > self.candidate_k:
            raise ValueError("final_k cannot exceed candidate_k")
        if self.context_max_tokens < 1:
            raise ValueError("context_max_tokens must be positive")

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StageTrace:
    """One case's immutable causal trace across the RAG stages."""

    case_id: str
    config_fingerprint: str
    query_variants: Tuple[QueryVariant, ...]
    source_rankings: Mapping[str, Sequence[str]]
    fused_chunk_ids: Tuple[str, ...]
    reranked_chunk_ids: Tuple[str, ...]
    packed_chunk_ids: Tuple[str, ...]
    answer: str = ""
    citations: Tuple[str, ...] = ()
    latency_ms: Mapping[str, float] = field(default_factory=dict)
    token_cost: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "config_fingerprint": self.config_fingerprint,
            "query_variants": [asdict(item) for item in self.query_variants],
            "source_rankings": {
                str(key): list(value) for key, value in self.source_rankings.items()
            },
            "fused_chunk_ids": list(self.fused_chunk_ids),
            "reranked_chunk_ids": list(self.reranked_chunk_ids),
            "packed_chunk_ids": list(self.packed_chunk_ids),
            "answer": self.answer,
            "citations": list(self.citations),
            "latency_ms": dict(self.latency_ms),
            "token_cost": dict(self.token_cost),
        }
