"""Versioned, fail-closed gate manifests owned by Evaluation."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class GateContractError(ValueError):
    pass


class GateState(str, Enum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    RUNNING = "RUNNING"
    DECIDED = "DECIDED"


class GateDecisionStatus(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    BLOCKED = "BLOCKED"


class ArtifactStatus(str, Enum):
    FROZEN = "FROZEN"
    VERIFIED = "VERIFIED"


@dataclass(frozen=True)
class Signature:
    role: str
    signer: str
    signed_at: str

    def __post_init__(self) -> None:
        _required(self.role, "signature.role")
        _required(self.signer, "signature.signer")
        _required(self.signed_at, "signature.signed_at")


@dataclass(frozen=True)
class TaskRef:
    id: str
    expected: str = "IMPLEMENTED"
    kind: str = "TaskRef"

    def __post_init__(self) -> None:
        _required(self.id, "TaskRef.id")
        if self.expected not in {"IMPLEMENTED", "VERIFIED"}:
            raise GateContractError("unknown TaskRef expected status")


@dataclass(frozen=True)
class GateDecisionRef:
    id: str
    expected: str = "APPROVE"
    kind: str = "GateDecisionRef"

    def __post_init__(self) -> None:
        _required(self.id, "GateDecisionRef.id")
        if self.expected != "APPROVE":
            raise GateContractError("unknown GateDecisionRef expected status")


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    checksum: str
    expected: str = ArtifactStatus.FROZEN.value
    kind: str = "ArtifactRef"

    def __post_init__(self) -> None:
        _required(self.id, "ArtifactRef.id")
        _sha256(self.checksum, "ArtifactRef.checksum")
        if self.expected not in {item.value for item in ArtifactStatus}:
            raise GateContractError("unknown ArtifactRef expected status")


@dataclass(frozen=True)
class ConditionalRequirement:
    id: str
    applicability_predicate: str
    evidence: str
    decision: str
    na_reason: str = ""
    na_approver: str = ""
    kind: str = "ConditionalRequirement"

    def __post_init__(self) -> None:
        _required(self.id, "ConditionalRequirement.id")
        _required(self.applicability_predicate, "applicability_predicate")
        _required(self.evidence, "ConditionalRequirement.evidence")
        if self.decision not in {"REQUIRED", "N/A"}:
            raise GateContractError("unknown ConditionalRequirement decision")
        if self.decision == "N/A" and not (
            self.na_reason.strip() and self.na_approver.strip()
        ):
            raise GateContractError("conditional N/A requires reason and approver")
        if self.decision == "REQUIRED" and (self.na_reason or self.na_approver):
            raise GateContractError("required conditional cannot carry N/A fields")


GatePrerequisite = TaskRef | GateDecisionRef | ArtifactRef | ConditionalRequirement


@dataclass(frozen=True)
class DatasetBinding:
    dataset_id: str
    manifest_path: str
    manifest_sha256: str
    cases_sha256: str

    def __post_init__(self) -> None:
        _required(self.dataset_id, "dataset_id")
        _required(self.manifest_path, "manifest_path")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _sha256(self.cases_sha256, "cases_sha256")


@dataclass(frozen=True)
class GateSpec:
    gate_id: str
    profile: str
    version: str
    prerequisites: tuple[GatePrerequisite, ...]
    datasets: tuple[DatasetBinding, ...]
    oracles: tuple[str, ...]
    zero_tolerance_properties: tuple[str, ...]
    statistical_thresholds: Mapping[str, Any]
    fault_injection_points: tuple[str, ...]
    cost_slo: Mapping[str, Any]
    evidence_owner: str
    independent_approvers: tuple[str, ...]
    rollback_conditions: tuple[str, ...]
    accountable_dri: str
    supersedes: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.gate_id, "gate_id"), (self.profile, "profile"),
            (self.version, "version"), (self.evidence_owner, "evidence_owner"),
            (self.accountable_dri, "accountable_dri"),
        ):
            _required(value, name)
        if not self.prerequisites:
            raise GateContractError("at least one prerequisite is required")
        if not all(isinstance(
            item, (TaskRef, GateDecisionRef, ArtifactRef, ConditionalRequirement),
        ) for item in self.prerequisites):
            raise GateContractError("unknown prerequisite type")
        ids = [item.id for item in self.prerequisites]
        if len(ids) != len(set(ids)):
            raise GateContractError("prerequisite IDs must be unique")
        if not self.datasets:
            raise GateContractError("at least one dataset binding is required")
        if not self.oracles:
            raise GateContractError("at least one oracle is required")
        if not self.zero_tolerance_properties:
            raise GateContractError("zero-tolerance properties are required")
        if not self.fault_injection_points:
            raise GateContractError("fault injection points are required")
        if not self.rollback_conditions:
            raise GateContractError("rollback conditions are required")
        if not self.independent_approvers:
            raise GateContractError("independent approver roles are required")
        if self.evidence_owner in self.independent_approvers:
            raise GateContractError("evidence owner cannot independently approve")
        _finite_numbers(self.statistical_thresholds, "statistical_thresholds")
        _finite_numbers(self.cost_slo, "cost_slo")


@dataclass(frozen=True)
class GateManifest:
    schema_version: int
    state: GateState
    spec: GateSpec
    created_at: str
    spec_sha256: str
    owner_signature: Signature | None = None
    approver_signature: Signature | None = None
    frozen_at: str = ""
    started_at: str = ""
    decided_at: str = ""
    decision_id: str = ""
    manifest_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["state"] = self.state.value
        raw["spec"]["prerequisites"] = [asdict(item) for item in self.spec.prerequisites]
        return raw


@dataclass(frozen=True)
class GateEvidence:
    evidence_id: str
    gate_id: str
    manifest_sha256: str
    producer_role: str
    producer: str
    created_at: str
    prerequisite_results: Mapping[str, str]
    artifacts: Mapping[str, str]
    observations: Mapping[str, Any]
    evidence_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateDecision:
    decision_id: str
    gate_id: str
    manifest_sha256: str
    evidence_sha256: str
    status: GateDecisionStatus
    reasons: tuple[str, ...]
    decided_at: str
    signature: Signature
    decision_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["status"] = self.status.value
        return raw


def draft_manifest(spec: GateSpec, *, created_at: str) -> GateManifest:
    _required(created_at, "created_at")
    manifest = GateManifest(
        schema_version=1,
        state=GateState.DRAFT,
        spec=spec,
        created_at=created_at,
        spec_sha256=_hash(_spec_dict(spec)),
    )
    return _seal_manifest(manifest)


def freeze_manifest(
    manifest: GateManifest,
    *,
    owner_signature: Signature,
    approver_signature: Signature,
    frozen_at: str,
) -> GateManifest:
    validate_manifest(manifest)
    if manifest.state is not GateState.DRAFT:
        raise GateContractError("only a DRAFT manifest can be frozen")
    if owner_signature.role != manifest.spec.evidence_owner:
        raise GateContractError("owner signature role does not match evidence owner")
    if approver_signature.role not in manifest.spec.independent_approvers:
        raise GateContractError("approver signature role is not authorized")
    if owner_signature.signer == approver_signature.signer:
        raise GateContractError("independent approver must be a different signer")
    return _seal_manifest(replace(
        manifest,
        state=GateState.FROZEN,
        owner_signature=owner_signature,
        approver_signature=approver_signature,
        frozen_at=_required(frozen_at, "frozen_at"),
        manifest_sha256="",
    ))


def start_gate(manifest: GateManifest, *, started_at: str) -> GateManifest:
    validate_manifest(manifest)
    if manifest.state is not GateState.FROZEN:
        raise GateContractError("only a FROZEN manifest can start")
    return _seal_manifest(replace(
        manifest,
        state=GateState.RUNNING,
        started_at=_required(started_at, "started_at"),
        manifest_sha256="",
    ))


def create_evidence(
    manifest: GateManifest,
    *,
    evidence_id: str,
    producer_role: str,
    producer: str,
    created_at: str,
    prerequisite_results: Mapping[str, str],
    artifacts: Mapping[str, str],
    observations: Mapping[str, Any],
) -> GateEvidence:
    validate_manifest(manifest)
    if manifest.state is not GateState.RUNNING:
        raise GateContractError("evidence can only bind a RUNNING manifest")
    if producer_role != manifest.spec.evidence_owner:
        raise GateContractError("evidence producer role does not match owner")
    expected_ids = {item.id for item in manifest.spec.prerequisites}
    if set(prerequisite_results) != expected_ids:
        raise GateContractError("evidence must report every prerequisite exactly once")
    for checksum in artifacts.values():
        _sha256(checksum, "artifact checksum")
    evidence = GateEvidence(
        evidence_id=_required(evidence_id, "evidence_id"),
        gate_id=manifest.spec.gate_id,
        manifest_sha256=manifest.manifest_sha256,
        producer_role=producer_role,
        producer=_required(producer, "producer"),
        created_at=_required(created_at, "created_at"),
        prerequisite_results=dict(prerequisite_results),
        artifacts=dict(artifacts),
        observations=dict(observations),
    )
    raw = evidence.to_dict()
    raw.pop("evidence_sha256", None)
    return replace(evidence, evidence_sha256=_hash(raw))


def decide_gate(
    manifest: GateManifest,
    evidence: GateEvidence,
    *,
    status: GateDecisionStatus,
    reasons: Sequence[str],
    signature: Signature,
    decided_at: str,
) -> tuple[GateManifest, GateDecision]:
    validate_manifest(manifest)
    validate_evidence(manifest, evidence)
    if signature.role not in manifest.spec.independent_approvers:
        raise GateContractError("decision signer is not an independent approver")
    if manifest.owner_signature and signature.signer == manifest.owner_signature.signer:
        raise GateContractError("evidence owner cannot decide its own gate")
    failures = prerequisite_failures(manifest.spec.prerequisites, evidence.prerequisite_results)
    if status is GateDecisionStatus.APPROVE and failures:
        raise GateContractError(f"cannot approve unmet prerequisites: {failures}")
    if status is not GateDecisionStatus.APPROVE and not reasons:
        raise GateContractError("non-approval decision requires reasons")
    decision_id = f"{manifest.spec.gate_id}:{manifest.spec.version}:decision"
    decision = GateDecision(
        decision_id=decision_id,
        gate_id=manifest.spec.gate_id,
        manifest_sha256=manifest.manifest_sha256,
        evidence_sha256=evidence.evidence_sha256,
        status=status,
        reasons=tuple(str(item) for item in reasons),
        decided_at=_required(decided_at, "decided_at"),
        signature=signature,
    )
    raw = decision.to_dict()
    raw.pop("decision_sha256", None)
    decision = replace(decision, decision_sha256=_hash(raw))
    decided = _seal_manifest(replace(
        manifest,
        state=GateState.DECIDED,
        decided_at=decision.decided_at,
        decision_id=decision.decision_id,
        manifest_sha256="",
    ))
    return decided, decision


def prerequisite_failures(
    prerequisites: Sequence[GatePrerequisite], results: Mapping[str, str],
) -> list[str]:
    failures = []
    for item in prerequisites:
        actual = str(results.get(item.id) or "MISSING")
        if isinstance(item, ConditionalRequirement):
            expected = "N/A" if item.decision == "N/A" else "SATISFIED"
        else:
            expected = item.expected
        if actual != expected:
            failures.append(f"{item.id}: expected {expected}, got {actual}")
    return failures


class GateStore:
    """One version per path; lifecycle updates preserve the frozen spec fingerprint."""

    def __init__(self, root: str | Path = "evaluation/gates"):
        self.root = Path(root)

    def path_for(self, manifest: GateManifest) -> Path:
        return self.root / manifest.spec.profile / f"{manifest.spec.version}.yaml"

    def save(self, manifest: GateManifest) -> Path:
        validate_manifest(manifest)
        path = self.path_for(manifest)
        if path.exists():
            prior = load_manifest(path)
            allowed = {
                GateState.DRAFT: GateState.FROZEN,
                GateState.FROZEN: GateState.RUNNING,
                GateState.RUNNING: GateState.DECIDED,
            }
            if allowed.get(prior.state) is not manifest.state:
                raise GateContractError("illegal or repeated persisted state transition")
            if prior.spec_sha256 != manifest.spec_sha256:
                raise GateContractError("frozen gate specification cannot change")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(path, manifest.to_dict())
        return path


def validate_manifest(manifest: GateManifest) -> None:
    if manifest.schema_version != 1:
        raise GateContractError("unsupported gate manifest schema")
    if manifest.spec_sha256 != _hash(_spec_dict(manifest.spec)):
        raise GateContractError("gate specification checksum mismatch")
    raw = manifest.to_dict()
    checksum = raw.pop("manifest_sha256", "")
    if _sha256(checksum, "manifest_sha256") != _hash(raw):
        raise GateContractError("gate manifest checksum mismatch")
    if manifest.state is not GateState.DRAFT:
        if not (manifest.owner_signature and manifest.approver_signature and manifest.frozen_at):
            raise GateContractError("frozen or later manifest must be signed")
    if manifest.state in {GateState.RUNNING, GateState.DECIDED} and not manifest.started_at:
        raise GateContractError("running or decided manifest requires started_at")
    if manifest.state is GateState.DECIDED and not (
        manifest.decided_at and manifest.decision_id
    ):
        raise GateContractError("decided manifest requires decision reference")


def validate_evidence(manifest: GateManifest, evidence: GateEvidence) -> None:
    if manifest.state is not GateState.RUNNING:
        raise GateContractError("decision requires a RUNNING manifest")
    if evidence.gate_id != manifest.spec.gate_id:
        raise GateContractError("evidence gate ID mismatch")
    if evidence.manifest_sha256 != manifest.manifest_sha256:
        raise GateContractError("evidence was produced for another manifest revision")
    raw = evidence.to_dict()
    checksum = raw.pop("evidence_sha256", "")
    if _sha256(checksum, "evidence_sha256") != _hash(raw):
        raise GateContractError("gate evidence checksum mismatch")


def load_manifest(path: str | Path) -> GateManifest:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(raw) != {
        "schema_version", "state", "spec", "created_at", "spec_sha256",
        "owner_signature", "approver_signature", "frozen_at", "started_at",
        "decided_at", "decision_id", "manifest_sha256",
    }:
        raise GateContractError("unknown or missing gate manifest fields")
    spec_raw = dict(raw["spec"])
    prerequisites = tuple(_prerequisite(item) for item in spec_raw.pop("prerequisites"))
    datasets = tuple(DatasetBinding(**item) for item in spec_raw.pop("datasets"))
    for field_name in (
        "oracles", "zero_tolerance_properties", "fault_injection_points",
        "independent_approvers", "rollback_conditions",
    ):
        spec_raw[field_name] = tuple(spec_raw[field_name])
    spec = GateSpec(prerequisites=prerequisites, datasets=datasets, **spec_raw)
    try:
        state = GateState(raw["state"])
    except ValueError as exc:
        raise GateContractError("unknown gate manifest state") from exc
    manifest = GateManifest(
        schema_version=int(raw["schema_version"]),
        state=state,
        spec=spec,
        created_at=raw["created_at"],
        spec_sha256=raw["spec_sha256"],
        owner_signature=Signature(**raw["owner_signature"]) if raw["owner_signature"] else None,
        approver_signature=Signature(**raw["approver_signature"]) if raw["approver_signature"] else None,
        frozen_at=raw["frozen_at"],
        started_at=raw["started_at"],
        decided_at=raw["decided_at"],
        decision_id=raw["decision_id"],
        manifest_sha256=raw["manifest_sha256"],
    )
    validate_manifest(manifest)
    return manifest


def load_evidence(path: str | Path) -> GateEvidence:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    evidence = GateEvidence(**raw)
    checksum = raw.pop("evidence_sha256", "")
    if _sha256(checksum, "evidence_sha256") != _hash(raw):
        raise GateContractError("gate evidence checksum mismatch")
    return evidence


def load_decision(path: str | Path) -> GateDecision:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        raw["status"] = GateDecisionStatus(raw["status"])
    except ValueError as exc:
        raise GateContractError("unknown gate decision status") from exc
    raw["reasons"] = tuple(raw["reasons"])
    raw["signature"] = Signature(**raw["signature"])
    decision = GateDecision(**raw)
    value = decision.to_dict()
    checksum = value.pop("decision_sha256", "")
    if _sha256(checksum, "decision_sha256") != _hash(value):
        raise GateContractError("gate decision checksum mismatch")
    return decision


def validate_gate_archive(
    manifest: GateManifest, evidence: GateEvidence, decision: GateDecision,
) -> None:
    validate_manifest(manifest)
    if manifest.state is not GateState.DECIDED:
        raise GateContractError("gate archive manifest must be DECIDED")
    running = _seal_manifest(replace(
        manifest,
        state=GateState.RUNNING,
        decided_at="",
        decision_id="",
        manifest_sha256="",
    ))
    validate_evidence(running, evidence)
    if decision.decision_id != manifest.decision_id:
        raise GateContractError("decision ID does not match manifest")
    if decision.gate_id != manifest.spec.gate_id:
        raise GateContractError("decision gate ID mismatch")
    if decision.manifest_sha256 != running.manifest_sha256:
        raise GateContractError("decision manifest revision mismatch")
    if decision.evidence_sha256 != evidence.evidence_sha256:
        raise GateContractError("decision evidence checksum mismatch")
    if decision.signature.role not in manifest.spec.independent_approvers:
        raise GateContractError("decision signer is not authorized")
    value = decision.to_dict()
    checksum = value.pop("decision_sha256", "")
    if _sha256(checksum, "decision_sha256") != _hash(value):
        raise GateContractError("gate decision checksum mismatch")
    failures = prerequisite_failures(
        manifest.spec.prerequisites, evidence.prerequisite_results,
    )
    if decision.status is GateDecisionStatus.APPROVE and failures:
        raise GateContractError("approved archive has unmet prerequisites")


def write_evidence(path: str | Path, evidence: GateEvidence) -> None:
    raw = evidence.to_dict()
    checksum = raw.pop("evidence_sha256", "")
    if _sha256(checksum, "evidence_sha256") != _hash(raw):
        raise GateContractError("gate evidence checksum mismatch")
    _write_once(Path(path), evidence.to_dict())


def write_decision(path: str | Path, decision: GateDecision) -> None:
    raw = decision.to_dict()
    checksum = raw.pop("decision_sha256", "")
    if _sha256(checksum, "decision_sha256") != _hash(raw):
        raise GateContractError("gate decision checksum mismatch")
    _write_once(Path(path), decision.to_dict())


def _prerequisite(raw: Mapping[str, Any]) -> GatePrerequisite:
    value = dict(raw)
    kind = value.pop("kind", "")
    types = {
        "TaskRef": TaskRef,
        "GateDecisionRef": GateDecisionRef,
        "ArtifactRef": ArtifactRef,
        "ConditionalRequirement": ConditionalRequirement,
    }
    if kind not in types:
        raise GateContractError("unknown prerequisite kind")
    return types[kind](**value)


def _seal_manifest(manifest: GateManifest) -> GateManifest:
    raw = manifest.to_dict()
    raw.pop("manifest_sha256", None)
    sealed = replace(manifest, manifest_sha256=_hash(raw))
    validate_manifest(sealed)
    return sealed


def _spec_dict(spec: GateSpec) -> dict[str, Any]:
    raw = asdict(spec)
    raw["prerequisites"] = [asdict(item) for item in spec.prerequisites]
    return raw


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(value: Any, name: str) -> str:
    text = str(value or "").lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise GateContractError(f"{name} must be a SHA-256 digest")
    return text


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GateContractError(f"{name} is required")
    return text


def _finite_numbers(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _finite_numbers(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _finite_numbers(child, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise GateContractError(f"{path} contains a non-finite number")


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise GateContractError(f"immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(path, value)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False,
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
