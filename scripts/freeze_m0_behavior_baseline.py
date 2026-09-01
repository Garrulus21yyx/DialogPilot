#!/usr/bin/env python3
"""Freeze a replayable M0 baseline from production-chain run records."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from core.model_policy import ModelPolicy
from evaluation.behavior_baseline import (
    BehaviorBaselineError,
    BehaviorRunRecord,
    build_behavior_baseline,
    write_behavior_baseline,
)
from services.evolution import AgentBundleRegistry


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_object(*args: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _classification(manifest: dict) -> str:
    searchable = json.dumps(manifest, ensure_ascii=False).lower()
    if "consumed_regression" in searchable or "consumed regression" in searchable:
        return "consumed_regression"
    if "auto_mapped" in searchable or "auto-mapped" in searchable:
        return "auto_mapped"
    return "provisional"


def _dataset(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases_path = path.parent / "cases.jsonl"
    cases_sha = _sha256(cases_path)
    if cases_sha != raw.get("cases_sha256"):
        raise BehaviorBaselineError(f"dataset checksum changed: {cases_path}")
    return {
        "dataset_id": raw.get("dataset_id"),
        "version": raw.get("version"),
        "classification": _classification(raw),
        "manifest_path": path.as_posix(),
        "manifest_sha256": _sha256(path),
        "cases_sha256": cases_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--records", required=True)
    parser.add_argument("--rag-index-manifest", required=True)
    parser.add_argument("--environment-observations", required=True)
    parser.add_argument("--bundle-db", required=True)
    parser.add_argument("--model-env", default=".env")
    parser.add_argument("--dataset-manifest", action="append", default=[])
    parser.add_argument("--baseline-id", default="m0-v1")
    parser.add_argument("--created-at", required=True)
    args = parser.parse_args()

    bundle = AgentBundleRegistry(args.bundle_db).active()
    runtime_model_policy = ModelPolicy.from_env({
        key: str(value)
        for key, value in dotenv_values(args.model_env).items()
        if value is not None
    }).to_dict()
    if dict(bundle.model_policy) != runtime_model_policy:
        raise BehaviorBaselineError(
            "active Bundle model policy does not match the runtime environment"
        )
    records = [
        BehaviorRunRecord.from_mapping(json.loads(line))
        for line in Path(args.records).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rag_manifest = json.loads(
        Path(args.rag_index_manifest).read_text(encoding="utf-8")
    )
    environment_observations = json.loads(
        Path(args.environment_observations).read_text(encoding="utf-8")
    )
    manifest = build_behavior_baseline(
        baseline_id=args.baseline_id,
        created_at=args.created_at,
        commit_sha=_git_object("HEAD"),
        tree_sha=_git_object("HEAD^{tree}"),
        bundle={"version": bundle.version, "content_hash": bundle.content_hash},
        model_policy=runtime_model_policy,
        rag_index_manifest=rag_manifest,
        environment_observations=environment_observations,
        datasets=[_dataset(Path(path)) for path in args.dataset_manifest],
        records=records,
    )
    write_behavior_baseline(args.output, manifest)
    print(json.dumps({
        "output": args.output,
        "manifest_sha256": manifest["manifest_sha256"],
        "route_count": len(manifest["route_measurements"]),
        "record_count": len(manifest["records"]),
        "production_accuracy_claim": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
