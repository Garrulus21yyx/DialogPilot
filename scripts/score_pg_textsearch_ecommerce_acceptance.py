"""Score pg_textsearch ecommerce evidence with the frozen source-span qrels."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from mcp.document_chunker import DocumentChunker
from scripts.report_ecommerce_complex import covers, metrics


DEFAULT_ROOT = Path("artifacts/eval/pg-textsearch-ecommerce40-v3-2026-09-09")
GOLD = {
    row["id"]: row
    for row in json.loads(Path("data/eval/ecommerce-complex-v2/dev.gold.json").read_text())
}
DOCS = {
    row["source_id"]: row
    for row in json.loads(
        Path("artifacts/eval/ecommerce-complex-v2-scoped-corpus-v2/corpus.json").read_text()
    )
}


def main(root: Path = DEFAULT_ROOT):
    rows = [json.loads(line) for line in gzip.open(root / "pure-cases.jsonl.gz", "rt")]
    assert len(rows) == 40 and {row["id"] for row in rows} == set(GOLD)
    universe = []
    for source_id in {
        unit["source_id"] for gold in GOLD.values() for unit in gold["evidence_units"]
    }:
        document = DOCS[source_id]
        universe.extend({
            "source_id": source_id,
            "source_start_char": chunk.start_char,
            "source_end_char": chunk.end_char,
            "content": chunk.content,
        } for chunk in DocumentChunker().split(
            document["content"],
            max_tokens=512,
            overlap_tokens=64,
            strategy="structure_aware",
            source_type=document["metadata"]["source_type"],
        ))
    scored = []
    for row in rows:
        units = GOLD[row["id"]]["evidence_units"]
        candidates = row["retrieval"][-1]["fused_candidates"] if row["retrieval"] else []
        ordered = row["rerank"][-1]["ordered_ids"] if row["rerank"] else []
        mapping = {item["chunk_id"]: item for item in candidates}
        wire = [{
            "source_id": item["source"]["source_id"],
            "source_start_char": item["source"]["start_char"],
            "source_end_char": item["source"]["end_char"],
            "content": item["text"],
        } for item in row["wire"].get("evidence", [])]
        if ordered:
            assert set(ordered) == set(mapping)
        for item in candidates + wire:
            source = DOCS[item["source_id"]]
            assert source["content"][
                item["source_start_char"]:item["source_end_char"]
            ] == item["content"]
        relevant = sum(any(covers(item, unit) for unit in units) for item in universe)
        scored.append({
            "id": row["id"],
            "status": row["result"]["status"],
            "candidate": metrics(candidates, units, relevant),
            "rerank": metrics([mapping[item] for item in ordered], units, relevant),
            "wire": metrics(wire, units, relevant),
            "wrong_scope": sum(
                item["source_id"] in GOLD[row["id"]]["forbidden_sources"]
                or item["source_id"].startswith("complex:scope-negative:")
                for item in wire
            ),
            "measured_ms": row["measured_ms"],
        })
    summary = {
        stage: {
            key: sum(row[stage][key] for row in scored) / len(scored)
            for key in scored[0][stage]
        }
        for stage in ("candidate", "rerank", "wire")
    }
    report = {
        "cases": len(scored),
        "summary": summary,
        "failures": [row["id"] for row in scored if row["status"] != "OK"],
        "wrong_scope_chunks": sum(row["wrong_scope"] for row in scored),
        "median_full_retrieval_wire_ms": sorted(row["measured_ms"] for row in scored)[19:21],
        "api_calls": 0,
        "scope": "actual retrieval, local CE, packing, ToolMessage; no answer generation",
    }
    (root / "scored.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n")
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    main(parser.parse_args().root)
