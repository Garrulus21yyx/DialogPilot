#!/usr/bin/env python3
"""Audit local conversation consumption before freezing a retrieval acceptance set."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.doc2dial_heldout_dataset import (
    DOMAINS,
    LENGTH_BUCKETS,
    _load_member,
    _maximum_history_case,
    document_length_bucket,
    freeze_doc2dial_heldout,
)
from evaluation.rag_provider_free import file_sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", required=True, type=Path)
    p.add_argument("--scan-root", required=True, action="append", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--per-domain", type=int, default=50)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")
    if args.per_domain < 1:
        raise ValueError("positive domain quota required")
    paths = []
    for root in args.scan_root:
        found = subprocess.run(
            ["rg", "--files", "--hidden", str(root), "-g", "cases.jsonl"],
            capture_output=True,
            text=True,
        )
        # rg can report unreadable unrelated system directories; never silently
        # omit relevant datasets: record stderr and the exact audited files.
        paths.extend(Path(x).resolve() for x in found.stdout.splitlines())
    seen = set()
    sources = []
    for path in sorted(set(paths)):
        rows = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
        groups = {
            str(r.get("group_id", ""))
            for r in rows
            if str(r.get("group_id", "")).startswith("doc2dial-")
        }
        if groups:
            seen.update(groups)
            sources.append(
                {
                    "path": str(path),
                    "sha256": file_sha(path),
                    "group_count": len(groups),
                }
            )
    if not seen:
        raise ValueError("no historical groups found")
    audit = {
        "scope": "Doc2Dial groups in discoverable local cases.jsonl files; not proof of unseen remote history",
        "scan_roots": [str(r.resolve()) for r in args.scan_root],
        "sources": sources,
        "excluded_group_ids": sorted(seen),
    }
    docs = _load_member(args.archive, "doc2dial_doc.json")["doc_data"]
    dials = _load_member(args.archive, "doc2dial_dial_test.json")["dial_data"]
    available = {(domain, bucket): 0 for domain in DOMAINS for bucket in LENGTH_BUCKETS}
    for domain in DOMAINS:
        for doc_id, conversations in dials[domain].items():
            document = docs[domain][doc_id]
            bucket = document_length_bucket(len(document["doc_text"]))
            for conversation in conversations:
                if f"doc2dial-{conversation['dial_id']}" in seen:
                    continue
                if _maximum_history_case(
                    domain=domain,
                    bucket=bucket,
                    document_id=doc_id,
                    document=document,
                    dialogue=conversation,
                ):
                    available[domain, bucket] += 1
    quotas = {key: 0 for key in available}
    for domain in DOMAINS:
        if sum(available[domain, b] for b in LENGTH_BUCKETS) < args.per_domain:
            raise ValueError(f"insufficient unconsumed cases in {domain}")
        for _ in range(args.per_domain):
            bucket = min(
                (b for b in LENGTH_BUCKETS if quotas[domain, b] < available[domain, b]),
                key=lambda b: (quotas[domain, b], LENGTH_BUCKETS.index(b)),
            )
            quotas[domain, bucket] += 1
    audit["eligible_remaining_by_stratum"] = {
        "/".join(k): v for k, v in available.items()
    }
    audit["frozen_quotas"] = {"/".join(k): v for k, v in quotas.items()}
    args.output.mkdir(parents=True)
    audit_path = args.output / "consumption-audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    dataset = freeze_doc2dial_heldout(
        args.archive,
        args.output / "dataset",
        excluded_dataset=(),
        excluded_group_ids=tuple(sorted(seen)),
        exclusion_audit_sha256=file_sha(audit_path),
        dataset_id="rag-d-doc2dial-local-unconsumed-200-v1",
        selection_seed="rag-d-acceptance-20260906-v1",
        stratum_quotas=quotas,
    )
    assert not seen & {c.group_id for c in dataset.cases}
    development = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "artifacts/eval/rag-d-provider-free-2026-09-06/dev56/report.json"
        ).read_text()
    )
    contract = {
        "embedding_model_files": development["model_identity"]["model_files"],
        "reranker_model_files": development["local_reranker_identity"],
        "status": "FROZEN_BEFORE_INFERENCE",
        "dataset_manifest_sha256": file_sha(dataset.root / "manifest.json"),
        "cases_sha256": dataset.manifest["cases_sha256"],
        "case_count": len(dataset.cases),
        "query_mode": "history",
        "chunk_tokens": 512,
        "overlap": 64,
        "strategy": "structure_aware",
        "candidate_policy": "flat",
        "dense_weights": [0.25],
        "source_k": 20,
        "final_k": 5,
        "evidence_text_tokens": 2600,
        "comparison": "no reranker vs local full-input BGE reranker, identical candidates",
        "configuration_selection_allowed": False,
        "external_inference_api_calls_allowed": 0,
        "limitation": "Fresh relative to enumerated local datasets; this is public retrieval acceptance, not ecommerce or final-answer acceptance.",
    }
    (args.output / "acceptance-contract.json").write_text(
        json.dumps(contract, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "excluded_groups": len(seen),
                "audited_files": len(sources),
                "frozen_cases": len(dataset.cases),
                "quotas": audit["frozen_quotas"],
            }
        )
    )


if __name__ == "__main__":
    main()
