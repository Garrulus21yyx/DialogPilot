"""Thin runner and aggregate retrieval metrics for Knowledge evaluation."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evaluation.command_primary_eval.contracts import Prediction
from evaluation.command_primary_eval.direct_runner import DirectRunner
from evaluation.command_primary_eval.knowledge import KnowledgeDirectAdapter


class KnowledgeDirectRunner(DirectRunner):
    def __init__(self, adapter: KnowledgeDirectAdapter) -> None:
        super().__init__(
            adapter,
            component="knowledge",
            evaluator_version="knowledge-direct-runner-v1",
            extra_dimensions=_retrieval_dimensions,
        )


def _retrieval_dimensions(
    predictions: Sequence[Prediction],
) -> Mapping[str, Any]:
    total = len(predictions)
    metrics = ("evidence_recall", "document_recall", "mrr", "ndcg")
    return {
        "retrieval": {
            name: (
                sum(
                    float(item.artifact["detail"]["metrics"][name])
                    for item in predictions
                ) / total
                if total else 0.0
            )
            for name in metrics
        },
    }
