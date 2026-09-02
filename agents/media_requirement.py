"""Agent-owned canonical media requirement decision contract (M5-T02A)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Mapping, Protocol, Sequence


class MediaRequirementError(ValueError):
    pass


class MediaRequirementMode(str, Enum):
    NO_MEDIA_REQUIRED = "NO_MEDIA_REQUIRED"
    MEDIA_TARGETS = "MEDIA_TARGETS"


class MediaNecessity(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class MediaStage(str, Enum):
    L1_TEXT_EXTRACTION = "L1_TEXT_EXTRACTION"
    L2_VISUAL_REASONING = "L2_VISUAL_REASONING"


NO_MEDIA_REASONS = frozenset({
    "MEDIA_IRRELEVANT", "TEXT_EVIDENCE_SUFFICIENT", "NO_RELEVANT_ASSET",
})


@dataclass(frozen=True)
class MediaRequirementBinding:
    media_binding_id: str
    requirement_id: str
    asset_id: str
    necessity: MediaNecessity
    required_stage: MediaStage
    reason_code: str
    region_key: str = ""
    omission_policy_ref: str = ""

    @classmethod
    def create(
        cls,
        *,
        requirement_id: str,
        asset_id: str,
        necessity: MediaNecessity,
        required_stage: MediaStage,
        reason_code: str,
        region_key: str = "",
        omission_policy_ref: str = "",
    ) -> "MediaRequirementBinding":
        values = {
            "requirement_id": _required(requirement_id, "requirement_id"),
            "asset_id": _required(asset_id, "asset_id"),
            "necessity": MediaNecessity(necessity).value,
            "required_stage": MediaStage(required_stage).value,
            "reason_code": _required(reason_code, "reason_code"),
            "region_key": str(region_key or "").strip(),
            "omission_policy_ref": str(omission_policy_ref or "").strip(),
        }
        media_binding_id = "media-binding:v1:" + hashlib.sha256(
            _canonical(values).encode()
        ).hexdigest()
        return cls(
            media_binding_id, values["requirement_id"], values["asset_id"],
            MediaNecessity(values["necessity"]), MediaStage(values["required_stage"]),
            values["reason_code"], values["region_key"], values["omission_policy_ref"],
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "MediaRequirementBinding":
        fields = {
            "media_binding_id", "requirement_id", "asset_id", "necessity",
            "required_stage", "reason_code", "region_key", "omission_policy_ref",
        }
        if set(raw) != fields:
            raise MediaRequirementError("media binding fields do not match v1 schema")
        binding = cls.create(
            requirement_id=str(raw["requirement_id"]), asset_id=str(raw["asset_id"]),
            necessity=MediaNecessity(str(raw["necessity"])),
            required_stage=MediaStage(str(raw["required_stage"])),
            reason_code=str(raw["reason_code"]), region_key=str(raw["region_key"]),
            omission_policy_ref=str(raw["omission_policy_ref"]),
        )
        if binding.media_binding_id != raw["media_binding_id"]:
            raise MediaRequirementError("media binding identity is not canonical")
        return binding

    def to_dict(self) -> Mapping[str, str]:
        return {
            "media_binding_id": self.media_binding_id,
            "requirement_id": self.requirement_id,
            "asset_id": self.asset_id,
            "necessity": self.necessity.value,
            "required_stage": self.required_stage.value,
            "reason_code": self.reason_code,
            "region_key": self.region_key,
            "omission_policy_ref": self.omission_policy_ref,
        }


@dataclass(frozen=True)
class MediaRequirementDecision:
    decision_id: str
    mode: MediaRequirementMode
    bindings: tuple[MediaRequirementBinding, ...]
    decision_reason_codes: tuple[str, ...]
    task_schema_hash: str
    policy_version: str
    producer: str = "Agent"
    schema_version: str = "media-requirement-decision-v1"

    @classmethod
    def create(
        cls,
        *,
        mode: MediaRequirementMode,
        bindings: Sequence[MediaRequirementBinding],
        decision_reason_codes: Sequence[str],
        task_schema_hash: str,
        policy_version: str,
        producer: str = "Agent",
    ) -> "MediaRequirementDecision":
        typed_mode = MediaRequirementMode(mode)
        typed_bindings = tuple(bindings)
        reasons = tuple(str(item).strip() for item in decision_reason_codes if str(item).strip())
        task_hash = str(task_schema_hash or "").strip()
        policy = _required(policy_version, "policy_version")
        if not re.fullmatch(r"[0-9a-f]{64}", task_hash):
            raise MediaRequirementError("task_schema_hash must be SHA-256")
        if producer != "Agent":
            raise MediaRequirementError("only Agent may produce media requirements")
        if typed_mode is MediaRequirementMode.NO_MEDIA_REQUIRED:
            if typed_bindings or len(reasons) != 1 or reasons[0] not in NO_MEDIA_REASONS:
                raise MediaRequirementError("NO_MEDIA_REQUIRED requires no bindings and one reason")
        elif not typed_bindings:
            raise MediaRequirementError("MEDIA_TARGETS requires bindings")
        ids = [item.media_binding_id for item in typed_bindings]
        if len(ids) != len(set(ids)):
            raise MediaRequirementError("duplicate media binding")
        for item in typed_bindings:
            rebuilt = MediaRequirementBinding.create(
                requirement_id=item.requirement_id, asset_id=item.asset_id,
                necessity=item.necessity, required_stage=item.required_stage,
                reason_code=item.reason_code, region_key=item.region_key,
                omission_policy_ref=item.omission_policy_ref,
            )
            if rebuilt.media_binding_id != item.media_binding_id:
                raise MediaRequirementError("media binding identity is not canonical")
        payload = {
            "mode": typed_mode.value, "bindings": [item.to_dict() for item in typed_bindings],
            "decision_reason_codes": reasons, "task_schema_hash": task_hash,
            "policy_version": policy, "producer": producer,
            "schema_version": "media-requirement-decision-v1",
        }
        decision_id = "media-decision:v1:" + hashlib.sha256(
            _canonical(payload).encode()
        ).hexdigest()
        return cls(
            decision_id, typed_mode, typed_bindings, reasons, task_hash, policy,
            producer,
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "MediaRequirementDecision":
        fields = {
            "decision_id", "mode", "bindings", "decision_reason_codes",
            "task_schema_hash", "policy_version", "producer", "schema_version",
        }
        if set(raw) != fields or raw.get("schema_version") != "media-requirement-decision-v1":
            raise MediaRequirementError("decision fields/version do not match v1 schema")
        bindings = raw["bindings"]
        reasons = raw["decision_reason_codes"]
        if not isinstance(bindings, list) or not isinstance(reasons, list):
            raise MediaRequirementError("decision bindings/reasons must be arrays")
        decision = cls.create(
            mode=MediaRequirementMode(str(raw["mode"])),
            bindings=tuple(MediaRequirementBinding.from_mapping(item) for item in bindings),
            decision_reason_codes=tuple(str(item) for item in reasons),
            task_schema_hash=str(raw["task_schema_hash"]),
            policy_version=str(raw["policy_version"]),
            producer=str(raw["producer"]),
        )
        if decision.decision_id != raw["decision_id"]:
            raise MediaRequirementError("media decision identity is not canonical")
        return decision

    def to_dict(self) -> Mapping[str, object]:
        return {
            "decision_id": self.decision_id,
            "mode": self.mode.value,
            "bindings": [item.to_dict() for item in self.bindings],
            "decision_reason_codes": list(self.decision_reason_codes),
            "task_schema_hash": self.task_schema_hash,
            "policy_version": self.policy_version,
            "producer": self.producer,
            "schema_version": self.schema_version,
        }

    @property
    def requires_aggregation(self) -> bool:
        return self.mode is MediaRequirementMode.MEDIA_TARGETS


@dataclass(frozen=True)
class RequirementMediaPolicy:
    required: bool
    minimum_stage: MediaStage
    allowed_omission_policy_refs: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MediaValidationContext:
    task_schema_hash: str
    policy_version: str
    allowed_assets: frozenset[str]
    allowed_regions: Mapping[str, frozenset[str]]
    requirements: Mapping[str, RequirementMediaPolicy]


@dataclass(frozen=True)
class MediaRequirementValidation:
    valid: bool
    code: str
    violations: tuple[str, ...] = ()


class MediaRequirementValidator:
    """Multimodal consumer validates; it never creates or rewrites decisions."""

    def validate(
        self, decision: MediaRequirementDecision, context: MediaValidationContext,
    ) -> MediaRequirementValidation:
        violations = []
        if decision.schema_version != "media-requirement-decision-v1":
            violations.append("UNKNOWN_SCHEMA_VERSION")
        if decision.producer != "Agent":
            violations.append("UNAUTHORIZED_PRODUCER")
        if decision.task_schema_hash != context.task_schema_hash:
            violations.append("TASK_SCHEMA_MISMATCH")
        if decision.policy_version != context.policy_version:
            violations.append("POLICY_VERSION_MISMATCH")
        for binding in decision.bindings:
            policy = context.requirements.get(binding.requirement_id)
            if policy is None:
                violations.append(f"UNKNOWN_REQUIREMENT:{binding.requirement_id}")
                continue
            if binding.asset_id not in context.allowed_assets:
                violations.append(f"ASSET_ACCESS_DENIED:{binding.asset_id}")
            if binding.region_key and binding.region_key not in context.allowed_regions.get(
                binding.asset_id, frozenset(),
            ):
                violations.append(f"INVALID_REGION:{binding.media_binding_id}")
            if policy.required and binding.necessity is MediaNecessity.OPTIONAL:
                violations.append(f"REQUIREDNESS_DOWNGRADE:{binding.requirement_id}")
            if (
                policy.minimum_stage is MediaStage.L2_VISUAL_REASONING
                and binding.required_stage is MediaStage.L1_TEXT_EXTRACTION
            ):
                violations.append(f"STAGE_DOWNGRADE:{binding.requirement_id}")
            if binding.necessity is MediaNecessity.OPTIONAL and (
                not binding.omission_policy_ref
                or binding.omission_policy_ref not in policy.allowed_omission_policy_refs
            ):
                violations.append(f"OMISSION_POLICY_DENIED:{binding.requirement_id}")
        return MediaRequirementValidation(
            not violations,
            "VALID" if not violations else "INVALID_MEDIA_REQUIREMENT",
            tuple(violations),
        )


class MediaRequirementProducer(Protocol):
    """Port implemented only by the Agent owner."""

    def decide(self, *args, **kwargs) -> MediaRequirementDecision: ...


def _required(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MediaRequirementError(f"{name} is required")
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
