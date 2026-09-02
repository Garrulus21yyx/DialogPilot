"""M5 Knowledge Gate remains unsigned until independent evidence exists."""
from pathlib import Path
import json

from evaluation.gates import GateState, TaskRef, draft_manifest, load_manifest
from scripts.create_m5_knowledge_gate_draft import build_spec


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "evaluation/gates/m5-knowledge-gate/v1.yaml"


def test_checked_in_m5_knowledge_gate_is_reproducible_and_unsigned():
    checked_in = load_manifest(GATE)
    rebuilt = draft_manifest(
        build_spec(ROOT), created_at="2026-09-02T20:00:00+02:00",
    )
    assert checked_in == rebuilt
    assert checked_in.state is GateState.DRAFT
    assert checked_in.owner_signature is None
    assert checked_in.approver_signature is None
    assert not (GATE.parent / "v1.evidence.json").exists()
    assert not (GATE.parent / "v1.decision.json").exists()


def test_gate_membership_matches_matrix_and_multimodal_is_non_blocking():
    manifest = load_manifest(GATE)
    assert {
        item.id for item in manifest.spec.prerequisites
        if isinstance(item, TaskRef)
    } == {"M5-T01", "X-T01", "X-T04"}
    assert all(not item.id.startswith("M5-T02") for item in manifest.spec.prerequisites)
    assert manifest.spec.independent_approvers == (
        "Evaluation", "Product/Support Ops",
    )


def test_provisional_heldout_cannot_satisfy_verified_threshold():
    dataset = json.loads((
        ROOT / "data/eval/knowledge-source-v0/manifest.json"
    ).read_text("utf-8"))
    assert dataset["status"] == "PROVISIONAL_NOT_GOLD"
    assert dataset["review"]["status"] == "REVIEW_REQUIRED"
    assert load_manifest(GATE).spec.statistical_thresholds[
        "fresh_heldout_review_status"
    ] == "VERIFIED"
