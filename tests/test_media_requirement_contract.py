"""M5-T02A Agent-owned media requirement schema and consumer validation."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from agents.media_requirement import (
    MediaNecessity,
    MediaRequirementBinding,
    MediaRequirementDecision,
    MediaRequirementError,
    MediaRequirementMode,
    MediaRequirementValidator,
    MediaStage,
    MediaValidationContext,
    RequirementMediaPolicy,
)


TASK_HASH = "a" * 64
ROOT = Path(__file__).resolve().parents[1]


def _binding(
    *, necessity=MediaNecessity.REQUIRED, stage=MediaStage.L2_VISUAL_REASONING,
    asset="asset-one", region="region-one", omission="",
):
    return MediaRequirementBinding.create(
        requirement_id="installation-step",
        asset_id=asset,
        necessity=necessity,
        required_stage=stage,
        reason_code="VISUAL_STEP_CONFIRMATION",
        region_key=region,
        omission_policy_ref=omission,
    )


def _decision(binding=None):
    return MediaRequirementDecision.create(
        mode=MediaRequirementMode.MEDIA_TARGETS,
        bindings=(binding or _binding(),),
        decision_reason_codes=("VISUAL_STEP_CONFIRMATION",),
        task_schema_hash=TASK_HASH,
        policy_version="media-policy-v1",
    )


def _context(*, required=True, minimum=MediaStage.L2_VISUAL_REASONING):
    return MediaValidationContext(
        task_schema_hash=TASK_HASH,
        policy_version="media-policy-v1",
        allowed_assets=frozenset({"asset-one"}),
        allowed_regions={"asset-one": frozenset({"region-one"})},
        requirements={
            "installation-step": RequirementMediaPolicy(
                required=required,
                minimum_stage=minimum,
                allowed_omission_policy_refs=frozenset({"omit-safe-v1"}),
            ),
        },
    )


def test_media_binding_and_decision_identities_are_canonical_and_replayable():
    decision = _decision()
    replay = MediaRequirementDecision.from_mapping(decision.to_dict())

    assert replay == decision
    assert decision.decision_id.startswith("media-decision:v1:")
    assert decision.bindings[0].media_binding_id.startswith("media-binding:v1:")
    assert decision.requires_aggregation is True


def test_no_media_requires_empty_bindings_and_has_no_aggregation_or_job_signal():
    decision = MediaRequirementDecision.create(
        mode=MediaRequirementMode.NO_MEDIA_REQUIRED,
        bindings=(),
        decision_reason_codes=("TEXT_EVIDENCE_SUFFICIENT",),
        task_schema_hash=TASK_HASH,
        policy_version="media-policy-v1",
    )

    assert decision.bindings == ()
    assert decision.requires_aggregation is False
    with pytest.raises(MediaRequirementError, match="requires no bindings"):
        MediaRequirementDecision.create(
            mode=MediaRequirementMode.NO_MEDIA_REQUIRED,
            bindings=(_binding(),),
            decision_reason_codes=("MEDIA_IRRELEVANT",),
            task_schema_hash=TASK_HASH,
            policy_version="media-policy-v1",
        )


def test_only_agent_can_produce_and_unknown_fields_or_version_fail_closed():
    with pytest.raises(MediaRequirementError, match="only Agent"):
        MediaRequirementDecision.create(
            mode=MediaRequirementMode.MEDIA_TARGETS, bindings=(_binding(),),
            decision_reason_codes=("VISUAL_STEP_CONFIRMATION",),
            task_schema_hash=TASK_HASH, policy_version="media-policy-v1",
            producer="Application",
        )
    payload = dict(_decision().to_dict())
    payload["runtime_hint"] = "please use vision"
    with pytest.raises(MediaRequirementError, match="fields/version"):
        MediaRequirementDecision.from_mapping(payload)
    payload.pop("runtime_hint")
    payload["schema_version"] = "future-v9"
    with pytest.raises(MediaRequirementError, match="fields/version"):
        MediaRequirementDecision.from_mapping(payload)


def test_validator_accepts_authorized_binding_without_rewriting_it():
    decision = _decision()
    result = MediaRequirementValidator().validate(decision, _context())

    assert result.valid is True
    assert result.code == "VALID"
    assert decision == _decision()


@pytest.mark.parametrize(
    ("binding", "context", "violation"),
    [
        (_binding(asset="asset-two"), _context(), "ASSET_ACCESS_DENIED"),
        (_binding(region="unknown"), _context(), "INVALID_REGION"),
        (
            _binding(necessity=MediaNecessity.OPTIONAL, omission="omit-safe-v1"),
            _context(required=True), "REQUIREDNESS_DOWNGRADE",
        ),
        (
            _binding(stage=MediaStage.L1_TEXT_EXTRACTION),
            _context(minimum=MediaStage.L2_VISUAL_REASONING), "STAGE_DOWNGRADE",
        ),
        (
            _binding(necessity=MediaNecessity.OPTIONAL, omission="unapproved"),
            _context(required=False), "OMISSION_POLICY_DENIED",
        ),
    ],
)
def test_acl_region_requiredness_stage_and_omission_downgrades_are_typed(
    binding, context, violation,
):
    result = MediaRequirementValidator().validate(_decision(binding), context)

    assert result.valid is False
    assert result.code == "INVALID_MEDIA_REQUIREMENT"
    assert any(item.startswith(violation) for item in result.violations)


def test_task_and_policy_version_drift_are_rejected_by_consumer():
    decision = _decision()
    context = replace(_context(), task_schema_hash="b" * 64, policy_version="future")
    result = MediaRequirementValidator().validate(decision, context)

    assert result.violations == ("TASK_SCHEMA_MISMATCH", "POLICY_VERSION_MISMATCH")


def test_frozen_media_contract_has_one_requirement_producer():
    contract = json.loads((
        ROOT / "governance/media/m5-t02a-media-requirement-v1.json"
    ).read_text(encoding="utf-8"))

    assert contract["producer_owner"] == "Agent"
    assert contract["consumer_owners"] == ["Multimodal"]
    assert contract["failure_code"] == "INVALID_MEDIA_REQUIREMENT"
    assert "Application" not in contract["consumer_owners"]
    assert "no aggregation, job, signal, OCR or VLM" in (
        contract["decision_modes"]["NO_MEDIA_REQUIRED"]
    )
