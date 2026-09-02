"""M1 Exit stays unsigned until production cutover evidence exists."""
import json
from pathlib import Path

from evaluation.gates import (
    ArtifactRef,
    ConditionalRequirement,
    GateState,
    TaskRef,
    draft_manifest,
    load_manifest,
)
from scripts.create_m1_exit_draft import build_spec


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "evaluation/gates/m1-exit/v1.yaml"


def test_checked_in_m1_exit_draft_is_reproducible_and_unsigned():
    checked_in = load_manifest(GATE)
    rebuilt = draft_manifest(
        build_spec(ROOT), created_at="2026-09-02T18:00:00+02:00",
    )

    assert checked_in == rebuilt
    assert checked_in.state is GateState.DRAFT
    assert checked_in.owner_signature is None
    assert checked_in.approver_signature is None
    assert not (GATE.parent / "v1.evidence.json").exists()
    assert not (GATE.parent / "v1.decision.json").exists()


def test_m1_membership_contains_concurrency_and_excludes_future_release_actions():
    manifest = load_manifest(GATE)
    prerequisites = manifest.spec.prerequisites
    task_ids = {
        item.id for item in prerequisites if isinstance(item, TaskRef)
    }

    assert {
        "M1-PF01", "M1-T00", "M1-T01", "M1-T02", "M1-T03", "M1-T04",
        "M1-T05", "M1-T02C", "M1-T02D", "M1-T03A", "M1-T04A",
        "M1-T04B", "X-T01", "X-T02",
    } == task_ids
    assert "M3-T09" not in task_ids
    artifact_ids = {
        item.id for item in prerequisites if isinstance(item, ArtifactRef)
    }
    assert artifact_ids == {
        "M1-PRODUCTION-SNAPSHOT-RESTORE", "M1-RESPONSE-DELIVERY-CUTOVER",
    }
    retention = next(
        item for item in prerequisites if isinstance(item, ConditionalRequirement)
    )
    assert retention.id == "M1-LEGACY-SQLITE-RETENTION"
    assert retention.decision == "REQUIRED"


def test_local_restore_artifacts_explicitly_cannot_satisfy_production_gate():
    foundation = json.loads((
        ROOT / "docs/data/postgres-foundation-restore-evidence-2026-09-02.json"
    ).read_text(encoding="utf-8"))
    delivery = json.loads((
        ROOT / "docs/data/response-delivery-cutover-restore-evidence-2026-09-02.json"
    ).read_text(encoding="utf-8"))

    assert "no production snapshot" in foundation["scope_limit"]
    assert "no production snapshot" in delivery["scope_limit"]
    assert foundation["verification"] == "PASS"
    assert delivery["reconciliation"] == "PASS"


def test_legacy_sqlite_inventory_has_explicit_owner_and_future_cutover():
    inventory = json.loads((
        ROOT / "docs/data/sqlite-owner-inventory-m1-pf01.json"
    ).read_text(encoding="utf-8"))
    by_store = {row["store"]: row for row in inventory["stores"]}

    assert by_store["RunStore"]["decision"] == "retain_then_retire"
    assert by_store["RunStore"]["cutover_owner_task"] == "M3-T06/M3-T09"
    assert by_store["TicketService"]["decision"] == "migrate"
    assert by_store["TicketService"]["cutover_owner_task"] == "M4-T07C"
    assert by_store["ResponseDelivery"]["decision"] == "migrate"
    assert by_store["ResponseDelivery"]["cutover_owner_task"] == "M1-T03A"
