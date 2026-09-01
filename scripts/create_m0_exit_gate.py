#!/usr/bin/env python3
"""Create the first frozen M0 gate archive from checked-in baseline evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from evaluation.behavior_baseline import load_behavior_baseline
from evaluation.gates import (
    DatasetBinding,
    GateDecisionStatus,
    GateSpec,
    GateStore,
    Signature,
    TaskRef,
    create_evidence,
    decide_gate,
    draft_manifest,
    freeze_manifest,
    start_gate,
    write_decision,
    write_evidence,
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output-root", default="evaluation/gates")
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--test-count", type=int, required=True)
    parser.add_argument("--owner-signer", required=True)
    parser.add_argument("--approver-signer", required=True)
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    baseline = load_behavior_baseline(baseline_path)
    datasets = tuple(DatasetBinding(
        dataset_id=item["dataset_id"],
        manifest_path=item["manifest_path"],
        manifest_sha256=item["manifest_sha256"],
        cases_sha256=item["cases_sha256"],
    ) for item in baseline["datasets"])
    spec = GateSpec(
        gate_id="M0-EXIT",
        profile="m0-exit",
        version="v1",
        prerequisites=tuple(TaskRef(f"M0-T0{index}") for index in range(1, 6)),
        datasets=datasets,
        oracles=(
            "public ChatOutcome characterization",
            "behavior-baseline schema/checksum replay",
            "owner stage evidence and stable invocation trace",
        ),
        zero_tolerance_properties=(
            "no protocol-owned business lifecycle",
            "no duplicate request identity",
            "no unbound case/request/trace/stage record",
            "no production accuracy claim from provisional data",
        ),
        statistical_thresholds={
            "scope": "characterization_only",
            "minimum_real_chain_cases": 7,
            "required_stage_observations_per_case": 9,
        },
        fault_injection_points=(
            "chat stage typed failure",
            "Bundle version/content drift",
            "RAG index manifest drift",
        ),
        cost_slo={
            "latency_and_call_counts": "reported_per_route",
            "enforcement": "not_applicable_for_m0_characterization",
        },
        evidence_owner="Evaluation",
        independent_approvers=("Application",),
        rollback_conditions=(
            "revert to the previous verified commit; do not restore a second business owner",
            "block M1 behavior rollout on baseline or application-boundary regression",
        ),
        accountable_dri="Evaluation",
    )
    owner = Signature("Evaluation", args.owner_signer, args.timestamp)
    approver = Signature("Application", args.approver_signer, args.timestamp)
    store = GateStore(args.output_root)
    draft = draft_manifest(spec, created_at=args.timestamp)
    store.save(draft)
    frozen = freeze_manifest(
        draft, owner_signature=owner, approver_signature=approver,
        frozen_at=args.timestamp,
    )
    store.save(frozen)
    running = start_gate(frozen, started_at=args.timestamp)
    store.save(running)
    baseline_records = baseline["records"]
    evidence = create_evidence(
        running,
        evidence_id="M0-EXIT-v1-evidence",
        producer_role="Evaluation",
        producer=args.owner_signer,
        created_at=args.timestamp,
        prerequisite_results={f"M0-T0{index}": "IMPLEMENTED" for index in range(1, 6)},
        artifacts={"m0_behavior_baseline": _file_sha256(baseline_path)},
        observations={
            "commit_sha": _git_head(),
            "test_count": args.test_count,
            "real_chain_record_count": len(baseline_records),
            "all_records_traceable": all(
                row.get("case_id") and row.get("request_id") and row.get("trace_id")
                and row.get("stages") for row in baseline_records
            ),
            "production_accuracy_claim": baseline["production_accuracy_claim"],
            "deployed_index_probe": baseline["environment_observations"][
                "deployed_index_probe"
            ],
        },
    )
    decided, decision = decide_gate(
        running,
        evidence,
        status=GateDecisionStatus.APPROVE,
        reasons=(),
        signature=approver,
        decided_at=args.timestamp,
    )
    store.save(decided)
    archive = store.path_for(decided).parent
    write_evidence(archive / "v1.evidence.json", evidence)
    write_decision(archive / "v1.decision.json", decision)
    print(json.dumps({
        "manifest": store.path_for(decided).as_posix(),
        "evidence": (archive / "v1.evidence.json").as_posix(),
        "decision": (archive / "v1.decision.json").as_posix(),
        "status": decision.status.value,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
