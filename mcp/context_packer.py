"""Deterministic, provenance-aware context packing for RAG generation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from memory.context import TokenEstimator


@dataclass(frozen=True)
class ContextCandidate:
    chunk_id: str
    document_id: str
    text: str
    start_char: int
    end_char: int
    title: str = ""
    score: float = 0.0
    ranks: tuple[tuple[str, int], ...] = ()
    source_type: str = ""
    source_checksum: str = ""
    source_revision: str = ""
    scope: str = "public"
    scope_decision: str = "allowed_public"
    index_manifest_fingerprint: str = ""


@dataclass(frozen=True)
class PackedContext:
    chunk_ids: tuple[str, ...]
    token_count: int
    skipped_redundant: tuple[str, ...] = ()
    skipped_budget: tuple[str, ...] = ()
    selected: tuple[ContextCandidate, ...] = ()


class ContextPacker:
    """Pack ranked chunks without exceeding the generation context contract."""

    def __init__(self, token_estimator: TokenEstimator | None = None):
        self._tokens = token_estimator or TokenEstimator()

    def pack(
        self,
        candidates: Sequence[ContextCandidate],
        *,
        max_tokens: int,
        max_chunks: int,
        redundancy_threshold: float = 1.0,
    ) -> PackedContext:
        if max_tokens < 1 or max_chunks < 1:
            raise ValueError("context budgets must be positive")
        if not 0 < redundancy_threshold <= 1:
            raise ValueError("redundancy_threshold must be in (0, 1]")
        selected: list[ContextCandidate] = []
        redundant, over_budget = [], []
        token_count = 0
        for candidate in candidates:
            if len(selected) >= max_chunks:
                break
            if any(_overlap_ratio(candidate, item) >= redundancy_threshold for item in selected):
                redundant.append(candidate.chunk_id)
                continue
            candidate_tokens = self._tokens.estimate(candidate.text)
            if token_count + candidate_tokens > max_tokens:
                over_budget.append(candidate.chunk_id)
                continue
            selected.append(candidate)
            token_count += candidate_tokens
        return PackedContext(
            tuple(item.chunk_id for item in selected), token_count,
            tuple(redundant), tuple(over_budget), tuple(selected),
        )


def _overlap_ratio(left: ContextCandidate, right: ContextCandidate) -> float:
    if left.document_id != right.document_id:
        return 0.0
    overlap = max(0, min(left.end_char, right.end_char) - max(left.start_char, right.start_char))
    shorter = min(left.end_char - left.start_char, right.end_char - right.start_char)
    return overlap / shorter if shorter > 0 else 0.0
