"""Deterministic weighted reciprocal-rank fusion shared by runtime and evaluation."""
from __future__ import annotations

from typing import Mapping, Sequence


def fuse_rankings(
    rankings: Mapping[str, Sequence[str]],
    *,
    weights: Mapping[str, float],
    rrf_k: int,
    top_k: int,
) -> list[str]:
    if rrf_k < 1 or top_k < 1:
        raise ValueError("rrf_k and top_k must be positive")
    if any(float(weight) < 0 for weight in weights.values()):
        raise ValueError("fusion weights must be non-negative")
    active = {str(source): float(weight) for source, weight in weights.items() if float(weight) > 0}
    if not active:
        raise ValueError("at least one fusion source must have positive weight")
    missing = sorted(set(active) - set(rankings))
    if missing:
        raise ValueError(f"rankings missing weighted sources: {missing}")
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for source, weight in active.items():
        seen = set()
        for rank, candidate_id in enumerate(rankings[source], start=1):
            candidate_id = str(candidate_id)
            if not candidate_id or candidate_id in seen:
                continue
            seen.add(candidate_id)
            scores[candidate_id] = scores.get(candidate_id, 0.0) + weight / (rrf_k + rank)
            best_rank[candidate_id] = min(best_rank.get(candidate_id, rank), rank)
    return sorted(
        scores,
        key=lambda candidate_id: (-scores[candidate_id], best_rank[candidate_id], candidate_id),
    )[:top_k]
