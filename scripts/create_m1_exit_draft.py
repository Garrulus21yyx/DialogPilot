#!/usr/bin/env python3
"""Create the unsigned M1 Exit draft without fabricating production evidence."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from evaluation.gates import (
    ArtifactRef,
    ConditionalRequirement,
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
    baseline = root / "data/eval/baselines/m0-v1"
    foundation_restore = (
        root / "docs/data/postgres-foundation-restore-evidence-2026-09-02.json"
    )
    delivery_restore = (
        root / "docs/data/response-delivery-cutover-restore-evidence-2026-09-02.json"
    )
    prerequisites = tuple(TaskRef(item) for item in (
        "M1-PF01", "M1-T00", "M1-T01", "M1-T02", "M1-T03", "M1-T04",
        "M1-T05", "M1-T02C", "M1-T02D", "M1-T03A", "M1-T04A",
        "M1-T04B", "X-T01", "X-T02",
    )) + (
        ArtifactRef(
            "M1-PRODUCTION-SNAPSHOT-RESTORE", _sha256(foundation_restore),
            expected="VERIFIED",
        ),
        ArtifactRef(
            "M1-RESPONSE-DELIVERY-CUTOVER", _sha256(delivery_restore),
            expected="VERIFIED",
        ),
        ConditionalRequirement(
            id="M1-LEGACY-SQLITE-RETENTION",
            applicability_predicate=(
                "TicketService or compatibility Agent runtime remains on SQLite"
            ),
            evidence=(
                "docs/data/sqlite-owner-inventory-m1-pf01.json + explicit future "
                "single-owner cutover tasks and read-only retirement rules"
            ),
            decision="REQUIRED",
        ),
    )
    return GateSpec(
        gate_id="M1-EXIT",
        profile="m1-exit",
        version="v1",
        prerequisites=prerequisites,
        datasets=(DatasetBinding(
            dataset_id="m0-v1-conversation-baseline",
            manifest_path="data/eval/baselines/m0-v1/manifest.json",
            manifest_sha256=_sha256(baseline / "manifest.json"),
            cases_sha256=_sha256(baseline / "cases.json"),
        ),),
        oracles=(
            "inbound-first durable turn/event/invocation/start-outbox transaction",
            "one durable compatibility claim and one response identity",
            "canonical final replay without model regeneration",
            "independent publication, delivery and read receipt lifecycle",
            "ordered real Redis/Chroma effects and deletion fencing",
            "registered durable locations and explicit legacy SQLite retention",
        ),
        zero_tolerance_properties=(
            "no inference before inbound durability",
            "no duplicate invocation, execution pointer or response publication",
            "no Redis, legacy Memory or SQLite fallback promoted to conversation owner",
            "no cross-tenant conversation or delivery read",
            "no late projection write after deletion epoch",
            "no unregistered subject-linked durable writer",
        ),
        statistical_thresholds={
            "duplicate_invocations": 0,
            "duplicate_response_identities": 0,
            "cross_tenant_reads": 0,
            "late_writes_after_deletion": 0,
            "production_restore_reconciliation_rate": 1.0,
            "production_delivery_cutover_reconciliation_rate": 1.0,
        },
        fault_injection_points=(
            "admission transaction after each durable fact",
            "start claim, stable run bind, invocation CAS and outbox ACK",
            "compatibility lease loss before ticket/final publication and after final commit",
            "publication commit and delivery ACK/read receipt",
            "projection effect before ACK and deletion during projection",
            "PostgreSQL primary loss and stale lease owner",
            "snapshot restore with count/hash reconciliation",
        ),
        cost_slo={
            "scope": "M1 durable data plane",
            "production_lock_rto_and_storage_budget": "required in deployment evidence",
        },
        evidence_owner="Application + Platform",
        independent_approvers=("Evaluation", "Security + Privacy"),
        rollback_conditions=(
            "freeze new admission/start/delivery/projection claims",
            "drain or reconcile in-flight stable identities without legacy replay",
            "retain PostgreSQL Conversation and switched Delivery as sole owners",
            "forward-fix or full restore; never delete committed conversation events",
        ),
        accountable_dri="Application",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-root", default="evaluation/gates")
    parser.add_argument("--timestamp", default="2026-09-02T18:00:00+02:00")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    manifest = draft_manifest(build_spec(root), created_at=args.timestamp)
    store = GateStore(root / args.output_root)
    path = store.path_for(manifest)
    if path.exists():
        if load_manifest(path) != manifest:
            raise RuntimeError("existing M1 Exit draft differs from generated contract")
    else:
        store.save(manifest)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
