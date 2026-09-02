"""X-T05 public capability claims remain traceable and bounded."""
import copy
import json
from pathlib import Path

import pytest

from scripts.validate_capability_claims import (
    ClaimValidationError,
    audit_registry,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "governance/claims/claim-registry-v1.json"


def test_every_registered_claim_resolves_all_six_evidence_classes():
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    claims = validate_registry(payload)

    assert len(claims) == 4
    assert all(claim["code_owner"] for claim in claims)
    assert all(claim["automated_tests"] for claim in claims)
    assert all(claim["data_manifest"]["sha256"] for claim in claims)
    assert all(claim["run_report"]["sha256"] for claim in claims)
    assert all(len(claim["commit"]) == 40 for claim in claims)
    assert all(claim["version"] and claim["limitations"] for claim in claims)


def test_seeded_random_claim_audit_is_reproducible():
    first = audit_registry(REGISTRY, sample_size=3, seed=20260902)
    second = audit_registry(REGISTRY, sample_size=3, seed=20260902)

    assert first == second
    assert first["result"] == "PASS"
    assert first["validated_claim_count"] == 4
    assert len(first["sampled_claim_ids"]) == 3
    frozen = json.loads((
        ROOT / "governance/evidence/x-t05/claim-audit-v1.json"
    ).read_text(encoding="utf-8"))
    assert frozen == first


def test_missing_or_drifted_evidence_fails_closed():
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    drifted["claims"][0]["run_report"]["sha256"] = "0" * 64

    with pytest.raises(ClaimValidationError, match="checksum mismatch"):
        validate_registry(drifted)


def test_claim_without_limitations_is_rejected():
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload["claims"][0]["limitations"] = []

    with pytest.raises(ClaimValidationError, match="limitations"):
        validate_registry(payload)
