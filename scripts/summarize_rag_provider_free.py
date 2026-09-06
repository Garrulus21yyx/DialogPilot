#!/usr/bin/env python3
"""Attribute saved development failures and compare pairs without model calls."""

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_ecommerce_dev import synthetic_development
from evaluation.rag_provider_free import file_sha


def load_rows(path):
    if path.exists():
        return [json.loads(line) for line in path.read_text().splitlines()]
    return [
        json.loads(line)
        for line in gzip.decompress(path.with_suffix(path.suffix + ".gz").read_bytes())
        .decode()
        .splitlines()
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")
    dataset = RagDataset.load(args.dataset)
    _, synthetic = synthetic_development()
    cases = {c.case_id: c for c in (*dataset.cases, *synthetic)}
    base = {
        r["case_id"]: r
        for r in load_rows(args.root / "dev56/cases.jsonl")
        if r["dense_weight"] == 0.25
    }
    reports = []
    for directory in sorted(args.root.iterdir()):
        if not directory.is_dir() or directory.name == "calibration":
            continue
        report = json.loads((directory / "report.json").read_text())
        chunks = {r["chunk_id"]: r for r in load_rows(directory / "chunks.jsonl")}
        for kind in ["cases", "reranked-cases"]:
            rows = load_rows(directory / (kind + ".jsonl"))
            for weight in [0.25, 0.5]:
                selected = {
                    r["case_id"]: r for r in rows if r["dense_weight"] == weight
                }
                if set(selected) != set(base):
                    raise ValueError("paired case set differs")
                failures = []
                for cid, row in selected.items():
                    if row["metrics"]["tool_message_complete"]:
                        continue
                    stage = next(
                        name
                        for name in [
                            "chunk_complete",
                            "candidate_complete",
                            "selected_complete",
                            "packed_complete",
                            "tool_message_complete",
                        ]
                        if not row["metrics"][name]
                    )
                    gold_docs = {span.document_id for span in cases[cid].evidence}
                    failures.append(
                        {
                            "case_id": cid,
                            "group_id": row["group_id"],
                            "first_loss": stage,
                            "query_used": row["query"],
                            "gold_parents_in_fused_top5": sorted(
                                gold_docs
                                & {
                                    chunks[chunk]["document_id"]
                                    for chunk in row["fused"][:5]
                                }
                            ),
                        }
                    )
                rescued = [
                    cid
                    for cid, r in selected.items()
                    if r["metrics"]["tool_message_complete"]
                    and not base[cid]["metrics"]["tool_message_complete"]
                ]
                harmed = [
                    cid
                    for cid, r in selected.items()
                    if not r["metrics"]["tool_message_complete"]
                    and base[cid]["metrics"]["tool_message_complete"]
                ]
                reports.append(
                    {
                        "variant": directory.name,
                        "reranked": kind == "reranked-cases",
                        "dense_weight": weight,
                        "case_count": len(selected),
                        "report_sha256": file_sha(directory / "report.json"),
                        "rescued": rescued,
                        "harmed": harmed,
                        "delta_pp": 100 * (len(rescued) - len(harmed)) / len(selected),
                        "failures": failures,
                        "candidate_complete": sum(
                            r["metrics"]["candidate_complete"]
                            for r in selected.values()
                        ),
                        "tool_message_complete": len(selected) - len(failures),
                    }
                )
    args.output.write_text(
        json.dumps(
            {
                "scope": "development failure attribution; no final-answer scoring",
                "baseline": "dev56 no reranker weight0.25",
                "variants": reports,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
