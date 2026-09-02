"""Closed Agent media-decision algebra and validator properties."""
from dataclasses import replace

import pytest

from application.media_requirement import (
    InvalidMediaRequirement,
    MediaNecessity,
    MediaRequirementBinding,
    MediaRequirementDecision,
    MediaRequirementMode,
    MediaRequirementPolicy,
    MediaRequirementValidator,
    MediaStage,
)


SCHEMA_HASH = "a" * 64
ASSET_ID = "asset:v1:allowed"


def _binding(**overrides):
    values = {
        "requirement_id": "media.error_screen",
        "asset_id": ASSET_ID,
        "necessity": MediaNecessity.REQUIRED,
        "required_stage": MediaStage.L2_VISUAL_REASONING,
        "reason_code": "VISUAL_EVIDENCE_REQUIRED",
    }
    values.update(overrides)
    return MediaRequirementBinding.create(**values)


def _policy(**overrides):
    values = {
        "requirement_id": "media.error_screen",
        "media_required": True,
        "required": True,
        "minimum_stage": MediaStage.L2_VISUAL_REASONING,
    }
    values.update(overrides)
    return MediaRequirementPolicy(**values)


def _decision(binding=None, **overrides):
    values = {
        "mode": MediaRequirementMode.MEDIA_TARGETS,
        "bindings": (_binding() if binding is None else binding,),
        "decision_reason_codes": ("VISUAL_EVIDENCE_REQUIRED",),
        "task_schema_hash": SCHEMA_HASH,
    }
    values.update(overrides)
    return MediaRequirementDecision.create(**values)


def _validate(decision, policies=None, assets=(ASSET_ID,)):
    return MediaRequirementValidator().validate(
        decision, policies=policies or (_policy(),),
        allowed_asset_ids=assets,
    )


def test_valid_agent_decision_preserves_deterministic_binding_and_decision_ids():
    decision = _decision()
    assert _validate(decision) is decision
    assert decision.bindings[0].media_binding_id.startswith("media-binding:v1:")
    assert decision.decision_id.startswith("media-decision:v1:")


def test_no_media_decision_has_no_jobs_or_bindings():
    decision = MediaRequirementDecision.create(
        mode=MediaRequirementMode.NO_MEDIA_REQUIRED,
        bindings=(), decision_reason_codes=("MEDIA_IRRELEVANT",),
        task_schema_hash=SCHEMA_HASH,
    )
    policy = _policy(media_required=False, required=False)
    assert _validate(decision, policies=(policy,)) is decision
    assert decision.bindings == ()


@pytest.mark.parametrize("mutate", [
    lambda decision: replace(decision, schema_version="media-v2"),
    lambda decision: replace(decision, policy_version="policy-v2"),
    lambda decision: replace(decision, producer_owner="multimodal"),
    lambda decision: replace(decision, task_schema_hash=""),
    lambda decision: replace(decision, decision_id="media-decision:v1:forged"),
])
def test_decision_rejects_unknown_version_owner_missing_schema_and_forged_id(mutate):
    with pytest.raises(InvalidMediaRequirement) as error:
        _validate(mutate(_decision()))
    assert error.value.code == "INVALID_MEDIA_REQUIREMENT"


def test_validator_rejects_asset_acl_and_region_violations():
    with pytest.raises(InvalidMediaRequirement, match="authorized turn"):
        _validate(_decision(), assets=("asset:v1:other",))
    invalid_region = _binding(region_key="../bad region")
    with pytest.raises(InvalidMediaRequirement, match="region"):
        _validate(_decision(invalid_region))


def test_validator_rejects_requiredness_and_stage_downgrades():
    optional = _binding(
        necessity=MediaNecessity.OPTIONAL,
        omission_policy_ref="optional-screenshot-v1",
    )
    with pytest.raises(InvalidMediaRequirement, match="marked optional"):
        _validate(_decision(optional), policies=(_policy(
            allowed_omission_policy_refs=("optional-screenshot-v1",),
        ),))
    l1 = _binding(required_stage=MediaStage.L1_TEXT_EXTRACTION)
    with pytest.raises(InvalidMediaRequirement, match="downgraded"):
        _validate(_decision(l1))


def test_optional_binding_requires_authorized_omission_policy():
    optional = _binding(
        necessity=MediaNecessity.OPTIONAL,
        omission_policy_ref="unknown-policy",
    )
    policy = _policy(
        media_required=False, required=False,
        minimum_stage=MediaStage.L1_TEXT_EXTRACTION,
        allowed_omission_policy_refs=("optional-screenshot-v1",),
    )
    with pytest.raises(InvalidMediaRequirement, match="omission"):
        _validate(_decision(optional), policies=(policy,))


def test_validator_rejects_duplicate_and_missing_required_bindings():
    binding = _binding()
    duplicate = _decision(bindings=(binding, binding))
    with pytest.raises(InvalidMediaRequirement, match="duplicate"):
        _validate(duplicate)
    no_media = MediaRequirementDecision.create(
        mode=MediaRequirementMode.NO_MEDIA_REQUIRED, bindings=(),
        decision_reason_codes=("NO_RELEVANT_ASSET",),
        task_schema_hash=SCHEMA_HASH,
    )
    with pytest.raises(InvalidMediaRequirement, match="missing"):
        _validate(no_media)
