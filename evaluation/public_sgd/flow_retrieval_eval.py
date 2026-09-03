"""Compare full Registry visibility with a non-authoritative Top-K shadow."""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Mapping

from application.flow_retrieval import LexicalFlowRetriever
from evaluation.public_sgd.runtime import SgdBenchmarkDataset


def evaluate_flow_retrieval(
    dataset: SgdBenchmarkDataset,
    *,
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> Mapping[str, Any]:
    retriever = LexicalFlowRetriever()
    hits: Counter[int] = Counter()
    eligible = 0
    latency = []
    for case in dataset.cases:
        required = {
            item["flow"]["flow_id"]
            for item in case["expected"].get("commands") or []
            if item.get("flow", {}).get("flow_id")
        }
        if not required:
            continue
        eligible += 1
        message, _state, history = dataset.materialize(case)
        query = " ".join((*[item["content"] for item in history], message))
        started = time.perf_counter()
        candidates = retriever.retrieve(query, dataset.registry, limit=max(ks))
        latency.append((time.perf_counter() - started) * 1000.0)
        ranked = [item.flow.flow_id for item in candidates]
        for k in ks:
            if required.issubset(ranked[:k]):
                hits[k] += 1
    full_count = len(dataset.registry.flows)
    return {
        "schema_version": "dialogpilot-flow-retrieval-shadow-v1",
        "behavior_effect": "NONE",
        "retriever_version": retriever.version,
        "eligible_cases": eligible,
        "full_registry_flow_count": full_count,
        "all_required_flow_recall": {
            str(k): hits[k] / eligible if eligible else 0.0 for k in ks
        },
        "candidate_reduction": {
            str(k): 1.0 - min(k, full_count) / full_count for k in ks
        },
        "latency_ms": {
            "mean": sum(latency) / len(latency) if latency else 0.0,
            "max": max(latency, default=0.0),
        },
    }
