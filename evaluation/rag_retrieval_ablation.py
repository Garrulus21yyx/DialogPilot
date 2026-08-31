"""Capture BM25/vector rankings once and replay explainable RRF weight ablations."""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.rag_pipeline.contracts import RagCase
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.fusion import fuse_rankings
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_pipeline.selection import paired_group_bootstrap_delta, select_configuration
from mcp.knowledge_base import KnowledgeBase


FUSION_CONFIGS = (
    ("bm25-only", 1.0, 0.0, 30),
    ("dense-only", 0.0, 1.0, 30),
    *((f"rrf-b{int(b * 100):02d}-v{int(v * 100):02d}-k{k}", b, v, k)
      for b, v in ((0.75, 0.25), (0.50, 0.50), (0.25, 0.75))
      for k in (10, 30, 60)),
)


def _mean(rows: Sequence[Mapping[str, float]], metric: str) -> float:
    return statistics.fmean(row[metric] for row in rows) if rows else 0.0


def run_retrieval_ablation(
    dataset: RagDataset,
    *,
    split: str,
    chunk_strategy: str,
    chunk_max_tokens: int,
    chunk_overlap_tokens: int,
    candidate_k: int = 20,
) -> dict[str, Any]:
    cases = dataset.select_cases(split)
    with tempfile.TemporaryDirectory(prefix="dialogpilot-rag-retrieval-") as temp_dir:
        knowledge_base = KnowledgeBase(
            chroma_mode="embedded",
            chroma_path=temp_dir,
            load_default_docs=False,
            collection_name="dialogpilot_rag_ablation",
            chunk_strategy=chunk_strategy,
            chunk_max_tokens=chunk_max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            retrieval_vector_weight=0.5,
            retrieval_lexical_weight=0.5,
        )
        inserted_chunks = knowledge_base.add_documents([{
            "id": document.document_id,
            "title": document.title,
            "content": document.content,
        } for document in dataset.documents])
        captures = []
        for case in cases:
            source_hits = {}
            latencies = {}
            for source, policy in (
                ("bm25", {"vector_weight": 0.0, "lexical_weight": 1.0}),
                ("vector", {"vector_weight": 1.0, "lexical_weight": 0.0}),
            ):
                started = time.monotonic()
                hits = knowledge_base.search(
                    case.query,
                    top_k=candidate_k,
                    retrieval_policy={**policy, "rrf_k": 30},
                )
                latencies[source] = (time.monotonic() - started) * 1000
                source_hits[source] = hits
            hit_by_id = {
                str(hit["chunk_id"]): hit
                for hits in source_hits.values() for hit in hits
            }
            captures.append({
                "case": case,
                "rankings": {
                    source: [str(hit["chunk_id"]) for hit in hits]
                    for source, hits in source_hits.items()
                },
                "hit_by_id": hit_by_id,
                "latencies": latencies,
            })

    rows = []
    per_config_case_metrics: dict[str, list[dict[str, float]]] = {}
    for config_id, bm25_weight, vector_weight, rrf_k in FUSION_CONFIGS:
        case_metrics = []
        slice_metrics: dict[str, list[dict[str, float]]] = defaultdict(list)
        estimated_latencies = []
        for capture in captures:
            active_weights = {
                source: weight for source, weight in (
                    ("bm25", bm25_weight), ("vector", vector_weight),
                ) if weight > 0
            }
            ranking = fuse_rankings(
                capture["rankings"], weights=active_weights,
                rrf_k=rrf_k, top_k=candidate_k,
            )
            metrics = evaluate_ranked_hits(
                capture["case"], ranking, capture["hit_by_id"], top_k=candidate_k,
            )
            case_metrics.append(metrics)
            for query_type in capture["case"].query_types:
                slice_metrics[query_type].append(metrics)
            active_latency = [
                capture["latencies"][source] for source in active_weights
            ]
            estimated_latencies.append(max(active_latency))
        per_config_case_metrics[config_id] = case_metrics
        rows.append({
            "config_id": config_id,
            "bm25_weight": bm25_weight,
            "vector_weight": vector_weight,
            "rrf_k": rrf_k,
            f"evidence_recall_at_{candidate_k}": _mean(case_metrics, "evidence_recall"),
            f"document_recall_at_{candidate_k}": _mean(case_metrics, "document_recall"),
            "mrr": _mean(case_metrics, "mrr"),
            f"ndcg_at_{candidate_k}": _mean(case_metrics, "ndcg"),
            "mean_estimated_retrieval_latency_ms": statistics.fmean(estimated_latencies),
            "slices": {
                name: {
                    f"evidence_recall_at_{candidate_k}": _mean(values, "evidence_recall"),
                    "mrr": _mean(values, "mrr"),
                }
                for name, values in sorted(slice_metrics.items())
            },
        })
    selection = select_configuration(
        rows,
        split=split,
        hard_constraints={},
        metric_priority=(
            (f"evidence_recall_at_{candidate_k}", "max"),
            ("mrr", "max"),
            (f"ndcg_at_{candidate_k}", "max"),
        ),
        cost_priority=("mean_estimated_retrieval_latency_ms",),
    )
    recommended = selection.get("recommended")
    uncertainty = {}
    if recommended:
        for baseline_id, baseline_metrics in per_config_case_metrics.items():
            if baseline_id == recommended:
                continue
            uncertainty[baseline_id] = {
                metric: paired_group_bootstrap_delta(
                    per_config_case_metrics[str(recommended)],
                    baseline_metrics,
                    group_ids=[case.group_id for case in cases],
                    metric=metric,
                )
                for metric in ("evidence_recall", "mrr", "ndcg")
            }
    return {
        "dataset_id": dataset.manifest["dataset_id"],
        "split": split,
        "case_count": len(cases),
        "document_count": len(dataset.documents),
        "inserted_chunks": inserted_chunks,
        "chunk_config": {
            "strategy": chunk_strategy,
            "max_tokens": chunk_max_tokens,
            "overlap_tokens": chunk_overlap_tokens,
        },
        "candidate_k": candidate_k,
        "ranking_capture": "bm25/vector captured once per case; RRF replayed offline",
        "selection": selection,
        "recommended_paired_bootstrap_vs": uncertainty,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--split", choices=("dev", "heldout", "stress"), default="dev")
    parser.add_argument("--chunk-strategy", choices=("fixed_tokens", "structure_aware"), default="fixed_tokens")
    parser.add_argument("--chunk-max-tokens", type=int, default=512)
    parser.add_argument("--chunk-overlap-tokens", type=int, default=64)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_retrieval_ablation(
        RagDataset.load(args.dataset),
        split=args.split,
        chunk_strategy=args.chunk_strategy,
        chunk_max_tokens=args.chunk_max_tokens,
        chunk_overlap_tokens=args.chunk_overlap_tokens,
        candidate_k=args.candidate_k,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "dataset_id": report["dataset_id"],
        "recommended": report["selection"]["recommended"],
        "inserted_chunks": report["inserted_chunks"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
