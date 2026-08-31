"""Compare bounded chunk configurations using source-span preservation metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_chunk_projection
from evaluation.rag_pipeline.selection import select_configuration


DEFAULT_CONFIGS = (
    ("fixed-256-0", "fixed_tokens", 256, 0),
    ("fixed-256-32", "fixed_tokens", 256, 32),
    ("fixed-384-0", "fixed_tokens", 384, 0),
    ("fixed-384-48", "fixed_tokens", 384, 48),
    ("fixed-512-0", "fixed_tokens", 512, 0),
    ("fixed-512-64", "fixed_tokens", 512, 64),
    ("structure-256-0", "structure_aware", 256, 0),
    ("structure-384-48", "structure_aware", 384, 48),
    ("structure-512-64", "structure_aware", 512, 64),
)


def run_chunk_ablation(dataset: RagDataset, *, split: str) -> dict:
    cases = dataset.select_cases(split)
    rows = []
    for config_id, strategy, max_tokens, overlap_tokens in DEFAULT_CONFIGS:
        metrics, chunks = evaluate_chunk_projection(
            dataset.documents,
            cases,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            strategy=strategy,
        )
        rows.append({
            "config_id": config_id,
            "strategy": strategy,
            "chunk_max_tokens": max_tokens,
            "chunk_overlap_tokens": overlap_tokens,
            "chunk_count": len(chunks),
            "strategy_complexity": 0 if strategy == "fixed_tokens" else 1,
            **metrics,
        })
    selection = select_configuration(
        rows,
        split=split,
        hard_constraints={},
        metric_priority=(
            ("evidence_containment_rate", "max"),
            ("boundary_fragmentation_rate", "min"),
        ),
        cost_priority=(
            "duplicate_char_ratio", "index_amplification", "chunks_per_document",
            "strategy_complexity",
        ),
    )
    return {
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_checksums": {
            "corpus": dataset.manifest["corpus_sha256"],
            "cases": dataset.manifest["cases_sha256"],
        },
        "split": split,
        "case_count": len(cases),
        "selection": selection,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--split", choices=("dev", "heldout", "stress"), default="dev")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_chunk_ablation(RagDataset.load(args.dataset), split=args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "dataset_id": report["dataset_id"],
        "case_count": report["case_count"],
        "recommended": report["selection"]["recommended"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
