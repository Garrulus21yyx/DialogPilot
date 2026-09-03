"""Thin runner and aggregate retrieval metrics for Memory RAG evaluation."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evaluation.command_primary_eval.contracts import Prediction
from evaluation.command_primary_eval.direct_runner import DirectRunner
from evaluation.command_primary_eval.memory import MemoryDirectAdapter


class MemoryDirectRunner(DirectRunner):
    def __init__(self, adapter: MemoryDirectAdapter) -> None:
        super().__init__(
            adapter,
            component="memory_rag",
            evaluator_version="memory-direct-runner-v1",
            extra_dimensions=_retrieval_dimensions,
        )


def _retrieval_dimensions(
    predictions: Sequence[Prediction],
) -> Mapping[str, Any]:
    recalls = [
        float(item.artifact["detail"]["recall_at_k"])
        for item in predictions
        if item.artifact["detail"]["recall_at_k"] is not None
    ]
    reciprocal_ranks = [
        float(item.artifact["detail"]["mrr"])
        for item in predictions
        if item.artifact["detail"]["mrr"] is not None
    ]
    return {
        "retrieval_quality": {
            "evaluated_cases": len(recalls),
            "mean_recall_at_k": (
                sum(recalls) / len(recalls) if recalls else None
            ),
            "mean_mrr": (
                sum(reciprocal_ranks) / len(reciprocal_ranks)
                if reciprocal_ranks else None
            ),
        },
    }
