"""Gate manifest algebra, lifecycle immutability and signing tests."""
from dataclasses import replace
import json

import pytest

from evaluation.gates import (
    ArtifactRef,
    ConditionalRequirement,
    DatasetBinding,
    GateContractError,
    GateDecisionRef,
    GateDecisionStatus,
    GateSpec,
    GateState,
    GateStore,
    Signature,
    TaskRef,
    create_evidence,
    decide_gate,
    draft_manifest,
    freeze_manifest,
    load_manifest,
    load_decision,
    load_evidence,
    start_gate,
    validate_gate_archive,
    write_decision,
    write_evidence,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _spec(*prerequisites):
    return GateSpec(
        gate_id="M0-EXIT",
        profile="m0-exit",
        version="v1",
        prerequisites=tuple(prerequisites or (
            TaskRef("M0-T01"),
            ArtifactRef("M0-BASELINE", SHA_A),
        )),
        datasets=(DatasetBinding(
            "m0-cases", "data/eval/baselines/m0-v1/cases.json",
            SHA_A, SHA_B,
        ),),
        oracles=("behavior-baseline-schema-v1",),
        zero_tolerance_properties=("no_unbound_trace",),
        statistical_thresholds={"minimum_cases": 7},
        fault_injection_points=("bundle_drift", "index_drift"),
        cost_slo={"report_only": True},
        evidence_owner="Evaluation",
        independent_approvers=("Application",),
        rollback_conditions=("restore CHAT_APPLICATION_V2=false",),
        accountable_dri="Evaluation",
    )


def _sign(role, signer):
    return Signature(role, signer, "2026-09-02T02:00:00+02:00")


def _running(spec=None):
    draft = draft_manifest(spec or _spec(), created_at="2026-09-02T01:00:00+02:00")
    frozen = freeze_manifest(
        draft,
        owner_signature=_sign("Evaluation", "evaluation-owner"),
        approver_signature=_sign("Application", "application-reviewer"),
        frozen_at="2026-09-02T02:00:00+02:00",
    )
    return start_gate(frozen, started_at="2026-09-02T02:01:00+02:00")


def test_manifest_lifecycle_is_linear_signed_and_replayable(tmp_path):
    store = GateStore(tmp_path)
    draft = draft_manifest(_spec(), created_at="2026-09-02T01:00:00+02:00")
    assert load_manifest(store.save(draft)) == draft
    frozen = freeze_manifest(
        draft,
        owner_signature=_sign("Evaluation", "evaluation-owner"),
        approver_signature=_sign("Application", "application-reviewer"),
        frozen_at="2026-09-02T02:00:00+02:00",
    )
    assert load_manifest(store.save(frozen)).state is GateState.FROZEN
    running = start_gate(frozen, started_at="2026-09-02T02:01:00+02:00")
    store.save(running)
    evidence = create_evidence(
        running,
        evidence_id="M0-EVIDENCE-v1",
        producer_role="Evaluation",
        producer="evaluation-owner",
        created_at="2026-09-02T02:02:00+02:00",
        prerequisite_results={"M0-T01": "IMPLEMENTED", "M0-BASELINE": "FROZEN"},
        artifacts={"behavior-baseline": SHA_A},
        observations={"tests_passed": 420},
    )
    decided, decision = decide_gate(
        running,
        evidence,
        status=GateDecisionStatus.APPROVE,
        reasons=(),
        signature=_sign("Application", "application-reviewer"),
        decided_at="2026-09-02T02:03:00+02:00",
    )
    store.save(decided)
    assert decision.status is GateDecisionStatus.APPROVE
    archived_manifest = load_manifest(store.path_for(decided))
    evidence_path = tmp_path / "evidence.json"
    decision_path = tmp_path / "decision.json"
    write_evidence(evidence_path, evidence)
    write_decision(decision_path, decision)
    archived_evidence = load_evidence(evidence_path)
    archived_decision = load_decision(decision_path)
    validate_gate_archive(archived_manifest, archived_evidence, archived_decision)
    assert archived_manifest.state is GateState.DECIDED


@pytest.mark.parametrize("transition", ["draft_to_running", "frozen_to_decided", "rerun"])
def test_illegal_lifecycle_transition_is_rejected(tmp_path, transition):
    store = GateStore(tmp_path)
    draft = draft_manifest(_spec(), created_at="2026-09-02T01:00:00+02:00")
    store.save(draft)
    frozen = freeze_manifest(
        draft,
        owner_signature=_sign("Evaluation", "owner"),
        approver_signature=_sign("Application", "reviewer"),
        frozen_at="2026-09-02T02:00:00+02:00",
    )
    if transition == "draft_to_running":
        with pytest.raises(GateContractError, match="persisted state transition"):
            store.save(start_gate(frozen, started_at="2026-09-02T02:01:00+02:00"))
    elif transition == "frozen_to_decided":
        store.save(frozen)
        running = start_gate(frozen, started_at="2026-09-02T02:01:00+02:00")
        evidence = create_evidence(
            running, evidence_id="e", producer_role="Evaluation", producer="owner",
            created_at="now", prerequisite_results={
                "M0-T01": "IMPLEMENTED", "M0-BASELINE": "FROZEN",
            }, artifacts={}, observations={},
        )
        decided, _ = decide_gate(
            running, evidence, status=GateDecisionStatus.APPROVE, reasons=(),
            signature=_sign("Application", "reviewer"), decided_at="later",
        )
        with pytest.raises(GateContractError, match="persisted state transition"):
            store.save(decided)
    else:
        store.save(frozen)
        with pytest.raises(GateContractError, match="persisted state transition"):
            store.save(frozen)


def test_threshold_or_dataset_change_after_freeze_is_rejected(tmp_path):
    store = GateStore(tmp_path)
    original_draft = draft_manifest(_spec(), created_at="original")
    original_frozen = freeze_manifest(
        original_draft, owner_signature=_sign("Evaluation", "owner"),
        approver_signature=_sign("Application", "reviewer"), frozen_at="later",
    )
    store.save(original_draft)
    store.save(original_frozen)

    changed_spec = replace(_spec(), statistical_thresholds={"minimum_cases": 1})
    changed_draft = draft_manifest(changed_spec, created_at="changed")
    changed_frozen = freeze_manifest(
        changed_draft, owner_signature=_sign("Evaluation", "owner"),
        approver_signature=_sign("Application", "reviewer"), frozen_at="later",
    )
    with pytest.raises(GateContractError, match="specification cannot change"):
        store.save(start_gate(changed_frozen, started_at="now"))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TaskRef("M0-T01", expected="N/A"),
        lambda: GateDecisionRef("M0-EXIT", expected="N/A"),
        lambda: ArtifactRef("BASELINE", SHA_A, expected="N/A"),
    ],
)
def test_only_conditional_requirement_can_be_na(factory):
    with pytest.raises(GateContractError, match="unknown"):
        factory()
    with pytest.raises(GateContractError, match="requires reason"):
        ConditionalRequirement("optional", "flag enabled", "flag=false", "N/A")
    conditional = ConditionalRequirement(
        "optional", "flag enabled", "flag=false", "N/A",
        na_reason="feature disabled", na_approver="Application",
    )
    running = _running(_spec(conditional))
    evidence = create_evidence(
        running, evidence_id="e", producer_role="Evaluation", producer="owner",
        created_at="now", prerequisite_results={"optional": "N/A"},
        artifacts={}, observations={},
    )
    _, decision = decide_gate(
        running, evidence, status=GateDecisionStatus.APPROVE, reasons=(),
        signature=_sign("Application", "reviewer"), decided_at="later",
    )
    assert decision.status is GateDecisionStatus.APPROVE


def test_missing_unknown_or_unmet_prerequisite_fails_closed():
    running = _running()
    with pytest.raises(GateContractError, match="every prerequisite"):
        create_evidence(
            running, evidence_id="e", producer_role="Evaluation", producer="owner",
            created_at="now", prerequisite_results={"M0-T01": "IMPLEMENTED"},
            artifacts={}, observations={},
        )
    evidence = create_evidence(
        running, evidence_id="e", producer_role="Evaluation", producer="owner",
        created_at="now", prerequisite_results={
            "M0-T01": "N/A", "M0-BASELINE": "FROZEN",
        }, artifacts={}, observations={},
    )
    with pytest.raises(GateContractError, match="cannot approve"):
        decide_gate(
            running, evidence, status=GateDecisionStatus.APPROVE, reasons=(),
            signature=_sign("Application", "reviewer"), decided_at="later",
        )


def test_manifest_requires_independent_signers_and_complete_spec():
    draft = draft_manifest(_spec(), created_at="now")
    with pytest.raises(GateContractError, match="different signer"):
        freeze_manifest(
            draft,
            owner_signature=_sign("Evaluation", "same"),
            approver_signature=_sign("Application", "same"),
            frozen_at="later",
        )
    with pytest.raises(GateContractError, match="at least one prerequisite"):
        _spec().__class__(**{**_spec().__dict__, "prerequisites": ()})


def test_linter_rejects_unknown_prerequisite_kind(tmp_path):
    manifest = draft_manifest(_spec(), created_at="now")
    raw = manifest.to_dict()
    raw["spec"]["prerequisites"][0]["kind"] = "NaturalLanguageRef"
    path = tmp_path / "invalid.yaml"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(GateContractError, match="unknown prerequisite kind"):
        load_manifest(path)
