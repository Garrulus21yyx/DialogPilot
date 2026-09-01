"""Build a small traceable parallel-condition stress split from Doc2Dial cases."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from evaluation.rag_pipeline.dataset import RagDataset, write_dataset


# Pair cases only within the same support domain so the combined request remains
# a plausible single customer turn. These IDs are immutable dataset provenance,
# not retrieval hints.
SOURCE_PAIRS = (
    ("doc2dial-dev-00a4a05f229e5cee83e7b10f2e20873d-12", "doc2dial-dev-07d2d3b557c6d5d18ee0e7d37e986f08-9"),
    ("doc2dial-dev-0f04d497049c31faebfa20487a25c0f1-12", "doc2dial-dev-56ecb9ca49fe6db3568b935cff6c0510-11"),
    ("doc2dial-dev-5c539f53cdb29706da2eac1fd093fb8a-10", "doc2dial-dev-e557d33d054041f0aac44defb1a207ef-12"),
    ("doc2dial-dev-a7fe7aa38830465e8a73320e9320b305-9", "doc2dial-dev-b30d73c202844505549d00218083577d-11"),
    ("doc2dial-dev-b3f2ae2fd2ddac2fb7a1a898690a6a8c-11", "doc2dial-dev-e96c4558ac2013c8f787498f67652362-11"),
    ("doc2dial-dev-6c00f80fa975923b3af5df055f965f2b-12", "doc2dial-dev-f9aa69339de7652e855b2c941a24d7c9-10"),
    ("doc2dial-dev-2278013a80847dedecfc48d80391fb36-13", "doc2dial-dev-2b9a9e5d3fb7afbd0daabf0fb8b00a54-11"),
    ("doc2dial-dev-4a335b1ca0ddb7d0465b81f2d318afd3-11", "doc2dial-dev-58d176e50ed5e9d07e84c19f97406e03-11"),
    ("doc2dial-dev-4d5350dc20418f7f89900dfa1a61b0cd-9", "doc2dial-dev-a9e6b11deba64bccfa874f154eef44b4-10"),
    ("doc2dial-dev-b09922c61586b9398efe4bd77271deef-11", "doc2dial-dev-fcc47e7aa1e49bd0de037d783936592b-13"),
    ("doc2dial-dev-1ce198bc767426637a333a1adc62e37a-12", "doc2dial-dev-e7545e6f53bbe7d12b6846061f8dd6ed-13"),
    ("doc2dial-dev-13f5099fe2dc8875b44e6b15ad7c2dd1-15", "doc2dial-dev-abba59c477b0cf795c20d5b0b83e4634-11"),
)


def build_parallel_stress(source: RagDataset, query_capture: dict, output: Path) -> None:
    cases = {case.case_id: case for case in source.cases}
    standalone = {
        str(row["case_id"]): str(row.get("standalone") or row.get("raw_query") or "")
        for row in query_capture.get("rows") or ()
    }
    output_cases = []
    capture_rows = []
    requirement_gold = []
    for index, pair in enumerate(SOURCE_PAIRS, 1):
        first, second = (cases[value] for value in pair)
        q1, q2 = (standalone[value].rstrip(" ?.!") for value in pair)
        query = f"I need help with two things: {q1}? Also, {q2}?"
        case_id = f"doc2dial-parallel-stress-{index:02d}"
        evidence = [asdict(item) for item in (*first.evidence, *second.evidence)]
        output_cases.append({
            "id": case_id,
            "group_id": case_id,
            "split": "stress",
            "query": query,
            "history": [],
            "evidence": evidence,
            "query_types": ["customer_support", "parallel_composite", "long_document"],
            "required_claims": [*first.required_claims, *second.required_claims],
            "forbidden_claims": [],
            "answerable": True,
        })
        capture_rows.append({
            "case_id": case_id,
            "group_id": case_id,
            "query_types": ["customer_support", "parallel_composite", "long_document"],
            "history_turns": 0,
            "raw_query": query,
            "standalone": query,
            "expansions": [],
            "hyde": "",
            "errors": [],
            "usage": {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0,
                      "latency_ms": {"sum": 0.0}},
            "usage_by_stage": {},
        })
        requirement_gold.append({
            "case_id": case_id,
            "source_case_ids": list(pair),
            "requirements": [
                {"requirement_id": "R1", "query": q1,
                 "evidence": [asdict(item) for item in first.evidence]},
                {"requirement_id": "R2", "query": q2,
                 "evidence": [asdict(item) for item in second.evidence]},
            ],
        })
    documents = [{
        "id": item.document_id,
        "title": item.title,
        "content": item.content,
        "metadata": dict(item.metadata),
    } for item in source.documents]
    dataset_id = "doc2dial-rag-parallel-stress-v1"
    write_dataset(
        output,
        dataset_id=dataset_id,
        documents=documents,
        cases=output_cases,
        source={
            "dataset": source.manifest.get("source", {}).get("dataset"),
            "license": source.manifest.get("source", {}).get("license"),
            "derived_from_dataset_id": source.manifest.get("dataset_id"),
            "construction": "12 same-domain pairs; query concatenation; Gold evidence union",
            "grounding_granularity": "character-span grouped by explicit requirement",
        },
    )
    query_capture_output = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "split": "stress",
        "sample_policy": "all deterministic parallel-composite cases",
        "case_count": len(capture_rows),
        "prompt_version": "deterministic-parallel-stress-v1",
        "model_policy": {"provider": "none", "rewrite": {"model": "none"}},
        "expansion_count": 0,
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "error_case_count": 0,
        "rows": capture_rows,
    }
    (output / "query-capture.json").write_text(
        json.dumps(query_capture_output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "requirement-gold.json").write_text(
        json.dumps({
            "schema_version": 1,
            "dataset_id": dataset_id,
            "gold_used_by_retrieval": False,
            "rows": requirement_gold,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--query-capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_parallel_stress(
        RagDataset.load(args.source),
        json.loads(args.query_capture.read_text(encoding="utf-8")),
        args.output,
    )
    print(json.dumps({"case_count": len(SOURCE_PAIRS), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
