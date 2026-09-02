"""M2 Exit stays unsigned until every internal and independent prerequisite exists."""
from pathlib import Path
import hashlib
import json

from evaluation.gates import (
    ArtifactRef,
    ConditionalRequirement,
    GateState,
    TaskRef,
    draft_manifest,
    load_manifest,
)
from scripts.create_m2_exit_draft import build_spec


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_checked_in_m2_exit_draft_is_reproducible_and_unsigned():
    path = ROOT / "evaluation/gates/m2-exit/v1.yaml"
    checked_in = load_manifest(path)
    rebuilt = draft_manifest(
        build_spec(ROOT), created_at="2026-09-02T12:00:00+02:00",
    )

    assert checked_in == rebuilt
    assert checked_in.state is GateState.DRAFT
    assert checked_in.owner_signature is None
    assert checked_in.approver_signature is None
    assert not (path.parent / "v1.evidence.json").exists()
    assert not (path.parent / "v1.decision.json").exists()


def test_m2_exit_membership_separates_build_gate_from_release_actions():
    manifest = load_manifest(ROOT / "evaluation/gates/m2-exit/v1.yaml")
    prerequisites = manifest.spec.prerequisites
    task_ids = {
        item.id for item in prerequisites if isinstance(item, TaskRef)
    }

    assert {"M2-PF01", "M2-T01", "M2-T06", "M2-T06R", "X-T03", "X-T04"} <= task_ids
    assert "M2-T05C" not in task_ids
    heldout = next(
        item for item in prerequisites
        if isinstance(item, ArtifactRef) and item.id == "M2-KNOWLEDGE-HELDOUT"
    )
    assert heldout.expected == "VERIFIED"
    assert heldout.checksum == _sha256(
        ROOT / "data/eval/knowledge-source-v0/heldout.jsonl"
    )
    authority = next(
        item for item in prerequisites
        if isinstance(item, ConditionalRequirement)
    )
    assert authority.id == "M2-AUTHORITY-ADAPTER-SET"
    assert authority.decision == "REQUIRED"


def test_provisional_knowledge_heldout_cannot_satisfy_verified_artifact():
    source_manifest = json.loads((
        ROOT / "data/eval/knowledge-source-v0/manifest.json"
    ).read_text(encoding="utf-8"))

    assert source_manifest["review"]["status"] == "REVIEW_REQUIRED"
    assert source_manifest["status"] == "PROVISIONAL_NOT_GOLD"
    assert source_manifest["review"]["reviewer"] is None
