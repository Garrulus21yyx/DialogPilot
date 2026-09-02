"""Validate public capability claims against immutable repository evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_MATURITY = {"TARGET", "EXPERIMENT", "IMPLEMENTED", "VERIFIED", "READY"}
REQUIRED_FIELDS = {
    "claim_id", "statement", "maturity", "scope", "code_owner", "code_paths",
    "automated_tests", "data_manifest", "run_report", "commit", "version",
    "limitations",
}


class ClaimValidationError(RuntimeError):
    pass


def _artifact(value: Any, *, claim_id: str, kind: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ClaimValidationError(f"{claim_id}: invalid {kind} reference")
    path = str(value["path"])
    digest = str(value["sha256"])
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise ClaimValidationError(f"{claim_id}: {kind} path escapes repository")
    target = ROOT / path
    if not target.is_file():
        raise ClaimValidationError(f"{claim_id}: missing {kind} {path}")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or actual != digest:
        raise ClaimValidationError(f"{claim_id}: {kind} checksum mismatch")
    return {"path": path, "sha256": digest}


def _existing_paths(value: Any, *, claim_id: str, kind: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ClaimValidationError(f"{claim_id}: {kind} must be non-empty")
    paths = tuple(str(item) for item in value)
    for path in paths:
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise ClaimValidationError(f"{claim_id}: {kind} path escapes repository")
        if not (ROOT / path).is_file():
            raise ClaimValidationError(f"{claim_id}: missing {kind} path {path}")
        if kind == "automated_tests" and not path.startswith("tests/test_"):
            raise ClaimValidationError(f"{claim_id}: non-test evidence path {path}")
    return paths


def _validate_commit(commit: str, *, claim_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ClaimValidationError(f"{claim_id}: commit must be a full SHA")
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT,
        capture_output=True, check=False,
    )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=ROOT,
        capture_output=True, check=False,
    )
    if exists.returncode or ancestor.returncode:
        raise ClaimValidationError(f"{claim_id}: commit is not in current history")


def validate_registry(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if payload.get("schema_version") != "capability-claim-v1":
        raise ClaimValidationError("unsupported claim registry schema")
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ClaimValidationError("claim registry must not be empty")
    seen: set[str] = set()
    validated = []
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != REQUIRED_FIELDS:
            raise ClaimValidationError("claim fields do not match the closed schema")
        claim_id = str(claim["claim_id"])
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", claim_id):
            raise ClaimValidationError(f"invalid claim_id: {claim_id}")
        if claim_id in seen:
            raise ClaimValidationError(f"duplicate claim_id: {claim_id}")
        seen.add(claim_id)
        for field in ("statement", "scope", "code_owner", "version"):
            if not str(claim[field]).strip():
                raise ClaimValidationError(f"{claim_id}: blank {field}")
        if claim["maturity"] not in ALLOWED_MATURITY:
            raise ClaimValidationError(f"{claim_id}: invalid maturity")
        limitations = claim["limitations"]
        if not isinstance(limitations, list) or not limitations or any(
            not str(item).strip() for item in limitations
        ):
            raise ClaimValidationError(f"{claim_id}: limitations must be explicit")
        _existing_paths(claim["code_paths"], claim_id=claim_id, kind="code_paths")
        _existing_paths(
            claim["automated_tests"], claim_id=claim_id, kind="automated_tests",
        )
        _artifact(claim["data_manifest"], claim_id=claim_id, kind="data_manifest")
        _artifact(claim["run_report"], claim_id=claim_id, kind="run_report")
        _validate_commit(str(claim["commit"]), claim_id=claim_id)
        validated.append(claim)
    return tuple(validated)


def audit_registry(
    registry_path: Path, *, sample_size: int, seed: int,
) -> dict[str, Any]:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    claims = validate_registry(payload)
    if sample_size < 1 or sample_size > len(claims):
        raise ClaimValidationError("sample_size is outside the registry")
    selected = random.Random(seed).sample(list(claims), sample_size)
    return {
        "registry_id": payload["registry_id"],
        "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "result": "PASS",
        "validated_claim_count": len(claims),
        "sample_seed": seed,
        "sampled_claim_ids": sorted(claim["claim_id"] for claim in selected),
        "checks": [
            "closed_schema", "owner_paths_exist", "automated_tests_exist",
            "manifest_checksum", "run_report_checksum", "commit_in_history",
            "explicit_scope_and_limitations",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry", type=Path,
        default=ROOT / "governance/claims/claim-registry-v1.json",
    )
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260902)
    args = parser.parse_args()
    print(json.dumps(
        audit_registry(args.registry, sample_size=args.sample_size, seed=args.seed),
        ensure_ascii=False, sort_keys=True, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
