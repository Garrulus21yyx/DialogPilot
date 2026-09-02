#!/usr/bin/env python3
"""Create the unsigned M5 Knowledge Gate draft without review fabrication."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from evaluation.gates import (
    DatasetBinding,
    GateSpec,
    GateStore,
    TaskRef,
    draft_manifest,
    load_manifest,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_spec(root: Path) -> GateSpec:
    dataset = root / "data/eval/knowledge-source-v0"
    return GateSpec(
        gate_id="M5-KNOWLEDGE-GATE",
        profile="m5-knowledge-gate",
        version="v1",
        prerequisites=tuple(TaskRef(item) for item in (
            "M5-T01", "X-T01", "X-T04",
        )),
        datasets=(DatasetBinding(
            dataset_id="knowledge-source-v0",
            manifest_path="data/eval/knowledge-source-v0/manifest.json",
            manifest_sha256=_sha256(dataset / "manifest.json"),
            cases_sha256=_sha256(dataset / "heldout.jsonl"),
        ),),
        oracles=(
            "canonical SourceRevision review and publication state machine",
            "Knowledge Publication scoped pointer CAS and request manifest pin",
            "previous verified manifest rollback with pinned in-flight completion",
            "Retriever conflict and terminal revision evidence fences",
            "feedback ticket and zero-hit remain non-publishing candidates",
        ),
        zero_tolerance_properties=(
            "no unknown or unlisted source lifecycle transition",
            "no global publication before KNOWLEDGE_LIFECYCLE_GA",
            "no request manifest drift after pointer switch or rollback",
            "no EvidenceReceipt payload from retracted or rejected revision",
            "no automatic candidate-to-publication path",
        ),
        statistical_thresholds={
            "illegal_transition_acceptances": 0,
            "cross_tenant_manifest_pins": 0,
            "partial_generation_observations": 0,
            "rollback_reconciliation_rate": 1.0,
            "fresh_heldout_review_status": "VERIFIED",
        },
        fault_injection_points=(
            "source lifecycle CAS before and after audit append",
            "manifest projection before generation READY",
            "scoped and global pointer CAS conflict",
            "request pin before concurrent pointer swap",
            "rollback before and after pointer commit",
            "retraction between retrieval and evidence dereference",
        ),
        cost_slo={
            "owner": "X-T04",
            "offline_ingest_budget": "required",
            "canary_latency_and_storage_budget": "requires measured gate evidence",
        },
        evidence_owner="Knowledge",
        independent_approvers=("Evaluation", "Product/Support Ops"),
        rollback_conditions=(
            "stop new scoped canary admission on any zero-tolerance violation",
            "atomically restore the previous verified manifest pointer",
            "allow in-flight requests to finish only against their immutable pins",
            "forward-fix lifecycle history; never rewrite revision or audit facts",
        ),
        accountable_dri="Knowledge",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-root", default="evaluation/gates")
    parser.add_argument("--timestamp", default="2026-09-02T20:00:00+02:00")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    manifest = draft_manifest(build_spec(root), created_at=args.timestamp)
    store = GateStore(root / args.output_root)
    path = store.path_for(manifest)
    if path.exists():
        if load_manifest(path) != manifest:
            raise RuntimeError("existing M5 Knowledge Gate draft differs from contract")
    else:
        store.save(manifest)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
