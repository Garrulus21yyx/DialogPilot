"""Select a context budget by evidence preservation, then minimum token cost."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Mapping

from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits, project_chunks
from evaluation.rag_pipeline.selection import select_configuration
from mcp.context_packer import ContextCandidate, ContextPacker


PACKING_CONFIGS = (
    ("top3-1800", 1800, 3, 1.0),
    ("top5-1200", 1200, 5, 1.0),
    ("top5-1800", 1800, 5, 1.0),
    ("top5-2600", 2600, 5, 1.0),
    ("dedup50-top5-1800", 1800, 5, 0.5),
    ("dedup50-top5-2600", 2600, 5, 0.5),
)


def run_packing_ablation(
    dataset: RagDataset,
    rerank_report: Mapping[str, Any],
    *,
    fixed_config_id: str | None = None,
) -> dict[str, Any]:
    chunks = project_chunks(
        dataset.documents, max_tokens=512, overlap_tokens=64, strategy="fixed_tokens",
    )
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    hits = {chunk.chunk_id: {
        "document_id": chunk.document_id,
        "source_start_char": chunk.start_char,
        "source_end_char": chunk.end_char,
    } for chunk in chunks}
    cases = {case.case_id: case for case in dataset.cases}
    packer = ContextPacker()
    result_rows = []
    per_config_cases = {}
    for config_id, token_budget, max_chunks, redundancy_threshold in PACKING_CONFIGS:
        metrics = []
        token_counts = []
        chunk_counts = []
        redundant_counts = []
        packed_rows = []
        for row in rerank_report["rows"]:
            case = cases[str(row["case_id"])]
            candidates = tuple(ContextCandidate(
                chunk_id=chunk_id,
                document_id=chunks_by_id[chunk_id].document_id,
                text=chunks_by_id[chunk_id].content,
                start_char=chunks_by_id[chunk_id].start_char,
                end_char=chunks_by_id[chunk_id].end_char,
            ) for chunk_id in row["reranked_ids"] if chunk_id in chunks_by_id)
            packed = packer.pack(
                candidates,
                max_tokens=token_budget,
                max_chunks=max_chunks,
                redundancy_threshold=redundancy_threshold,
            )
            metric = evaluate_ranked_hits(case, packed.chunk_ids, hits, top_k=max_chunks)
            metrics.append(metric)
            token_counts.append(packed.token_count)
            chunk_counts.append(len(packed.chunk_ids))
            redundant_counts.append(len(packed.skipped_redundant))
            packed_rows.append({
                "case_id": case.case_id,
                "packed_chunk_ids": list(packed.chunk_ids),
                "token_count": packed.token_count,
            })
        per_config_cases[config_id] = packed_rows
        result_rows.append({
            "config_id": config_id,
            "token_budget": token_budget,
            "max_chunks": max_chunks,
            "redundancy_threshold": redundancy_threshold,
            "evidence_recall": statistics.fmean(item["evidence_recall"] for item in metrics),
            "mrr": statistics.fmean(item["mrr"] for item in metrics),
            "ndcg": statistics.fmean(item["ndcg"] for item in metrics),
            "mean_packed_tokens": statistics.fmean(token_counts),
            "mean_packed_chunks": statistics.fmean(chunk_counts),
            "mean_skipped_redundant": statistics.fmean(redundant_counts),
        })
    target_recall = float(rerank_report["llm_rerank"]["evidence_recall_at_5"])
    selection = select_configuration(
        result_rows,
        split=str(rerank_report["split"]),
        hard_constraints={"evidence_recall": ("ge", target_recall)},
        metric_priority=(("evidence_recall", "max"),),
        cost_priority=("mean_packed_tokens", "mean_packed_chunks"),
    )
    recommended = selection.get("recommended")
    evaluated_config = str(recommended or fixed_config_id or "")
    if evaluated_config and evaluated_config not in per_config_cases:
        raise ValueError(f"unknown fixed packing config: {evaluated_config}")
    return {
        "dataset_id": rerank_report["dataset_id"],
        "split": rerank_report["split"],
        "case_count": rerank_report["case_count"],
        "target_contract": (
            "packing must preserve the measured reranked top-5 evidence recall; "
            "among qualifying configs choose maximum recall then minimum tokens"
        ),
        "target_evidence_recall": target_recall,
        "selection": selection,
        "results": result_rows,
        "evaluated_config": evaluated_config or None,
        "recommended_rows": per_config_cases.get(evaluated_config, []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("rerank_report", type=Path)
    parser.add_argument(
        "--fixed-config",
        choices=tuple(item[0] for item in PACKING_CONFIGS),
        help="Frozen Dev-selected config to report on heldout; never used for selection",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_packing_ablation(
        RagDataset.load(args.dataset),
        json.loads(args.rerank_report.read_text(encoding="utf-8")),
        fixed_config_id=args.fixed_config,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "recommended": report["selection"]["recommended"],
        "target_evidence_recall": report["target_evidence_recall"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
