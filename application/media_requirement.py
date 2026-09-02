"""Agent-owned contract for deciding whether a task needs media processing."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Mapping, Protocol, Sequence


class InvalidMediaRequirement(ValueError):
    """Typed fail-closed outcome for an invalid media decision."""

    code = "INVALID_MEDIA_REQUIREMENT"


class MediaRequirementMode(str, Enum):
    NO_MEDIA_REQUIRED = "NO_MEDIA_REQUIRED"
    MEDIA_TARGETS = "MEDIA_TARGETS"


class MediaNecessity(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class MediaStage(IntEnum):
    L1_TEXT_EXTRACTION = 1
    L2_VISUAL_REASONING = 2


NO_MEDIA_REASON_CODES = frozenset({
    "MEDIA_IRRELEVANT",
    "TEXT_EVIDENCE_SUFFICIENT",
    "NO_RELEVANT_ASSET",
})
MEDIA_TARGET_REASON_CODES = frozenset({
    "TEXT_EXTRACTION_REQUIRED",
    "VISUAL_EVIDENCE_REQUIRED",
    "TARGET_REGION_REQUIRED",
})
_REGION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SCHEMA_VERSION = "media-requirement-decision-v1"
_POLICY_VERSION = "media-requirement-policy-v1"


@dataclass(frozen=True)
class MediaRequirementBinding:
    media_binding_id: str
    requirement_id: str
    asset_id: str
    necessity: MediaNecessity
    required_stage: MediaStage
    reason_code: str
    region_key: str | None = None
    omission_policy_ref: str | None = None

    @classmethod
    def create(
        cls,
        *,
        requirement_id: str,
        asset_id: str,
        necessity: MediaNecessity,
        required_stage: MediaStage,
        reason_code: str,
        region_key: str | None = None,
        omission_policy_ref: str | None = None,
    ) -> "MediaRequirementBinding":
        values = {
            "requirement_id": requirement_id,
            "asset_id": asset_id,
            "region_key": region_key or "",
            "necessity": MediaNecessity(necessity).value,
            "required_stage": MediaStage(required_stage).name,
        }
        digest = _fingerprint(values)
        return cls(
            media_binding_id=f"media-binding:v1:{digest}",
            requirement_id=requirement_id,
            asset_id=asset_id,
            region_key=region_key,
            necessity=MediaNecessity(necessity),
            required_stage=MediaStage(required_stage),
            reason_code=reason_code,
            omission_policy_ref=omission_policy_ref,
        )

    @property
    def expected_id(self) -> str:
        return self.create(
            requirement_id=self.requirement_id,
            asset_id=self.asset_id,
            region_key=self.region_key,
            necessity=self.necessity,
            required_stage=self.required_stage,
            reason_code=self.reason_code,
            omission_policy_ref=self.omission_policy_ref,
        ).media_binding_id


@dataclass(frozen=True)
class MediaRequirementDecision:
    decision_id: str
    mode: MediaRequirementMode
    bindings: tuple[MediaRequirementBinding, ...]
    decision_reason_codes: tuple[str, ...]
    task_schema_hash: str
    policy_version: str = _POLICY_VERSION
    schema_version: str = _SCHEMA_VERSION
    producer_owner: str = "agent"

    @classmethod
    def create(
        cls,
        *,
        mode: MediaRequirementMode,
        bindings: Sequence[MediaRequirementBinding],
        decision_reason_codes: Sequence[str],
        task_schema_hash: str,
        producer_owner: str = "agent",
        policy_version: str = _POLICY_VERSION,
    ) -> "MediaRequirementDecision":
        canonical = {
            "mode": MediaRequirementMode(mode).value,
            "binding_ids": [item.media_binding_id for item in bindings],
            "decision_reason_codes": list(decision_reason_codes),
            "task_schema_hash": task_schema_hash,
            "policy_version": policy_version,
            "schema_version": _SCHEMA_VERSION,
            "producer_owner": producer_owner,
        }
        return cls(
            decision_id=f"media-decision:v1:{_fingerprint(canonical)}",
            mode=MediaRequirementMode(mode),
            bindings=tuple(bindings),
            decision_reason_codes=tuple(decision_reason_codes),
            task_schema_hash=task_schema_hash,
            policy_version=policy_version,
            producer_owner=producer_owner,
        )


@dataclass(frozen=True)
class MediaRequirementPolicy:
    """Authority-owned constraints consumed by the multimodal validator."""

    requirement_id: str
    media_required: bool
    required: bool
    minimum_stage: MediaStage
    allowed_omission_policy_refs: tuple[str, ...] = ()


class MediaRequirementProducerPort(Protocol):
    """Only an Agent implementation may produce the canonical decision."""

    async def decide_media_requirement(
        self, *, task: Mapping[str, Any], asset_ids: tuple[str, ...],
    ) -> MediaRequirementDecision: ...


class MediaRequirementValidator:
    version = _POLICY_VERSION

    def validate(
        self,
        decision: MediaRequirementDecision,
        *,
        policies: Sequence[MediaRequirementPolicy],
        allowed_asset_ids: Sequence[str],
    ) -> MediaRequirementDecision:
        try:
            self._validate(decision, policies, frozenset(allowed_asset_ids))
        except (TypeError, ValueError, KeyError) as exc:
            if isinstance(exc, InvalidMediaRequirement):
                raise
            raise InvalidMediaRequirement(str(exc)) from exc
        return decision

    def _validate(
        self,
        decision: MediaRequirementDecision,
        policies: Sequence[MediaRequirementPolicy],
        allowed_assets: frozenset[str],
    ) -> None:
        if decision.schema_version != _SCHEMA_VERSION:
            raise InvalidMediaRequirement("unsupported media decision schema")
        if decision.policy_version != self.version:
            raise InvalidMediaRequirement("unsupported media policy version")
        if decision.producer_owner != "agent":
            raise InvalidMediaRequirement("media decision producer is not Agent owner")
        if not re.fullmatch(r"[0-9a-f]{64}", decision.task_schema_hash):
            raise InvalidMediaRequirement("task schema hash is required")
        if len(set(decision.decision_reason_codes)) != len(
            decision.decision_reason_codes
        ) or not decision.decision_reason_codes:
            raise InvalidMediaRequirement("decision reason codes are invalid")
        expected = MediaRequirementDecision.create(
            mode=decision.mode, bindings=decision.bindings,
            decision_reason_codes=decision.decision_reason_codes,
            task_schema_hash=decision.task_schema_hash,
            producer_owner=decision.producer_owner,
            policy_version=decision.policy_version,
        ).decision_id
        if decision.decision_id != expected:
            raise InvalidMediaRequirement("media decision identity drift")

        policy_by_id = {item.requirement_id: item for item in policies}
        if len(policy_by_id) != len(policies):
            raise InvalidMediaRequirement("duplicate media authority policy")
        if decision.mode is MediaRequirementMode.NO_MEDIA_REQUIRED:
            if decision.bindings:
                raise InvalidMediaRequirement("NO_MEDIA_REQUIRED must have no bindings")
            if not set(decision.decision_reason_codes).issubset(NO_MEDIA_REASON_CODES):
                raise InvalidMediaRequirement("invalid no-media reason code")
            if any(item.media_required for item in policies):
                raise InvalidMediaRequirement("required media binding is missing")
            return

        if not decision.bindings:
            raise InvalidMediaRequirement("MEDIA_TARGETS requires bindings")
        if not set(decision.decision_reason_codes).issubset(
            MEDIA_TARGET_REASON_CODES
        ):
            raise InvalidMediaRequirement("invalid media-target reason code")
        binding_ids = [item.media_binding_id for item in decision.bindings]
        if len(set(binding_ids)) != len(binding_ids):
            raise InvalidMediaRequirement("duplicate media binding")

        covered_requirements = set()
        for binding in decision.bindings:
            policy = policy_by_id.get(binding.requirement_id)
            if policy is None:
                raise InvalidMediaRequirement("unknown media requirement")
            if binding.asset_id not in allowed_assets:
                raise InvalidMediaRequirement("asset is outside the authorized turn")
            if binding.media_binding_id != binding.expected_id:
                raise InvalidMediaRequirement("media binding identity drift")
            if binding.region_key is not None and not _REGION_KEY.fullmatch(
                binding.region_key
            ):
                raise InvalidMediaRequirement("invalid media region")
            if binding.reason_code not in MEDIA_TARGET_REASON_CODES:
                raise InvalidMediaRequirement("invalid media binding reason")
            if binding.required_stage < policy.minimum_stage:
                raise InvalidMediaRequirement("media stage was downgraded")
            if policy.required and binding.necessity is not MediaNecessity.REQUIRED:
                raise InvalidMediaRequirement("required media was marked optional")
            if binding.necessity is MediaNecessity.OPTIONAL:
                if (
                    not binding.omission_policy_ref
                    or binding.omission_policy_ref
                    not in policy.allowed_omission_policy_refs
                ):
                    raise InvalidMediaRequirement("optional media omission is unauthorized")
            elif binding.omission_policy_ref is not None:
                raise InvalidMediaRequirement("required media cannot carry omission policy")
            covered_requirements.add(binding.requirement_id)
        missing = {
            item.requirement_id for item in policies if item.media_required
        } - covered_requirements
        if missing:
            raise InvalidMediaRequirement(
                f"required media requirements are missing: {sorted(missing)}"
            )


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
