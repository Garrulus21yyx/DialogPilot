#!/usr/bin/env python3
"""Create the unsigned M2 Exit draft without fabricating review evidence."""
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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def build_spec(root: Path) -> GateSpec:
    dataset = root / "data/eval/knowledge-source-v0"
    heldout_sha = _sha256(dataset / "heldout.jsonl")
    prerequisites = tuple(TaskRef(item) for item in (
        "M2-PF01", "M2-T01", "M2-T02", "M2-T03", "M2-T04", "M2-T05",
        "M2-T06", "M2-T01A", "M2-T04A", "M2-T06A", "M2-T06R",
        "X-T03", "X-T04",
    )) + (
        ArtifactRef(
            "M2-KNOWLEDGE-HELDOUT", heldout_sha, expected="VERIFIED",
        ),
        ConditionalRequirement(
            id="M2-AUTHORITY-ADAPTER-SET",
            applicability_predicate=(
                "supported route authorities include live business facts/actions"
            ),
            evidence="AuthorityPolicyRegistry + governed tool manifests",
            decision="REQUIRED",
        ),
    )
    return GateSpec(
        gate_id="M2-EXIT",
        profile="m2-exit",
        version="v1",
        prerequisites=prerequisites,
        datasets=(DatasetBinding(
            dataset_id="knowledge-source-v0",
            manifest_path="data/eval/knowledge-source-v0/manifest.json",
            manifest_sha256=_sha256(dataset / "manifest.json"),
            cases_sha256=heldout_sha,
        ),),
        oracles=(
            "eight RouteMode execution/outcome/forbidden-call algebra",
            "FactRequirement and canonical EvidenceReceipt coverage",
            "single-owner KnowledgeRetriever and frozen PG dark-shadow report",
            "single-publisher Bundle rollback with pinned in-flight refs",
        ),
        zero_tolerance_properties=(
            "no cross-tenant evidence or cache reuse",
            "no unauthorized or duplicate business write",
            "no dynamic claim without authoritative requirement coverage",
            "no duplicate answer publisher or Handoff write from shadow",
            "no retrieval after deletion fence or silent generation fallback",
        ),
        statistical_thresholds={
            "route_modes_covered": 8,
            "forbidden_call_violations": 0,
            "maximum_publishers_per_invocation": 1,
            "hard_safety_failures": 0,
            "knowledge_heldout_review_status": "VERIFIED",
        },
        fault_injection_points=(
            "active generation pointer swap",
            "route Bundle active pointer swap",
            "rollback after canary admission stop",
            "Redis cache unavailable or corrupt",
            "tool write unknown effect and approval replay",
            "conversation deletion during projection/retrieval",
        ),
        cost_slo={
            "owner": "X-T04",
            "route_budget_enforcement": "required",
            "provider_usage_reconciliation": "required",
        },
        evidence_owner="Application + Agent + Platform",
        independent_approvers=("Evaluation", "Domain Tool"),
        rollback_conditions=(
            "any zero-tolerance property violation blocks canary admission",
            "atomically restore the previous compatible Bundle/read pointer",
            "if previous is incompatible, block admission and forward-fix",
        ),
        accountable_dri="Application",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-root", default="evaluation/gates")
    parser.add_argument("--timestamp", default="2026-09-02T12:00:00+02:00")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    manifest = draft_manifest(build_spec(root), created_at=args.timestamp)
    store = GateStore(root / args.output_root)
    path = store.path_for(manifest)
    if path.exists():
        if load_manifest(path) != manifest:
            raise RuntimeError("existing M2 Exit draft differs from generated contract")
    else:
        store.save(manifest)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
