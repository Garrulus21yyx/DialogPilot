"""Versioned contract for the selective command Encoder artifact."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from application.command_encoder_input import RENDERER_SHA256, RENDERER_VERSION
from application.turn_state import FlowDefinitionRef
from application.turn_understanding import CommandKind


ARTIFACT_SCHEMA = "dialogpilot-command-encoder-artifact-v1"
SUPERVISION_SCHEMA = "dialogpilot-command-encoder-supervision-v1"
DEFER_LABEL = "__DEFER__"


class CommandEncoderArtifactError(ValueError):
    pass


def _sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise CommandEncoderArtifactError(f"{name} must be lowercase SHA-256")


def _nonblank(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CommandEncoderArtifactError(f"{name} is required")


@dataclass(frozen=True)
class CommandEncoderTarget:
    command_kind: CommandKind
    flow: FlowDefinitionRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.command_kind, CommandKind):
            raise CommandEncoderArtifactError("target command kind is invalid")
        if self.flow is not None and not isinstance(self.flow, FlowDefinitionRef):
            raise CommandEncoderArtifactError("target flow reference is invalid")

    @property
    def label(self) -> str:
        flow = f"{self.flow.flow_id}@{self.flow.version}" if self.flow else "-"
        return f"{self.command_kind.value}|{flow}"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "label": self.label,
            "command_kind": self.command_kind.value,
            "flow_id": self.flow.flow_id if self.flow else None,
            "flow_version": self.flow.version if self.flow else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommandEncoderTarget":
        flow_id = value.get("flow_id")
        flow_version = value.get("flow_version")
        if (flow_id is None) != (flow_version is None):
            raise CommandEncoderArtifactError("target flow identity is incomplete")
        target = cls(
            CommandKind(str(value["command_kind"])),
            (
                FlowDefinitionRef(str(flow_id), str(flow_version))
                if flow_id is not None else None
            ),
        )
        if value.get("label") != target.label:
            raise CommandEncoderArtifactError("target label does not match its command")
        return target


@dataclass(frozen=True)
class CandidateCalibration:
    target_label: str
    threshold: float
    enabled: bool
    accepted_count: int
    correct_count: int
    precision_lower_bound: float

    def __post_init__(self) -> None:
        _nonblank(self.target_label, "calibration target")
        if isinstance(self.threshold, bool) or not 0.0 <= self.threshold <= 1.0:
            raise CommandEncoderArtifactError("calibration threshold must be in [0, 1]")
        if self.accepted_count < 0 or not 0 <= self.correct_count <= self.accepted_count:
            raise CommandEncoderArtifactError("calibration acceptance counts are invalid")
        if not 0.0 <= self.precision_lower_bound <= 1.0:
            raise CommandEncoderArtifactError(
                "calibration precision lower bound must be in [0, 1]"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_label": self.target_label,
            "threshold": self.threshold,
            "enabled": self.enabled,
            "accepted_count": self.accepted_count,
            "correct_count": self.correct_count,
            "precision_lower_bound": self.precision_lower_bound,
        }


@dataclass(frozen=True)
class DatasetDigest:
    split: str
    sha256: str

    def __post_init__(self) -> None:
        if self.split not in {"train", "calibration", "heldout"}:
            raise CommandEncoderArtifactError("unsupported dataset split")
        _sha256(self.sha256, f"{self.split} dataset digest")

    def to_dict(self) -> dict[str, str]:
        return {"split": self.split, "sha256": self.sha256}


@dataclass(frozen=True)
class CommandEncoderArtifactManifest:
    artifact_version: str
    registry_fingerprint: str
    encoder_provider: str
    encoder_model: str
    encoder_revision: str
    encoder_sha256: str
    encoder_dimension: int
    encoder_profile_fingerprint: str
    classifier_filename: str
    classifier_sha256: str
    classifier_format: str
    calibration_method: str
    calibration_version: str
    target_precision: float
    targets: tuple[CommandEncoderTarget, ...]
    calibrations: tuple[CandidateCalibration, ...]
    datasets: tuple[DatasetDigest, ...]
    candidate_limit: int = 3
    schema_version: str = ARTIFACT_SCHEMA
    status: str = "OFFLINE_CANDIDATE"
    renderer_version: str = RENDERER_VERSION
    renderer_sha256: str = RENDERER_SHA256
    defer_label: str = DEFER_LABEL

    def __post_init__(self) -> None:
        for value, name in (
            (self.artifact_version, "artifact version"),
            (self.encoder_provider, "encoder provider"),
            (self.encoder_model, "encoder model"),
            (self.encoder_revision, "encoder revision"),
            (self.classifier_filename, "classifier filename"),
            (self.classifier_format, "classifier format"),
            (self.calibration_method, "calibration method"),
            (self.calibration_version, "calibration version"),
        ):
            _nonblank(value, name)
        for value, name in (
            (self.registry_fingerprint, "registry fingerprint"),
            (self.encoder_sha256, "encoder digest"),
            (self.encoder_profile_fingerprint, "encoder profile fingerprint"),
            (self.classifier_sha256, "classifier digest"),
            (self.renderer_sha256, "renderer digest"),
        ):
            _sha256(value, name)
        if self.schema_version != ARTIFACT_SCHEMA:
            raise CommandEncoderArtifactError("unsupported artifact schema")
        if self.status != "OFFLINE_CANDIDATE":
            raise CommandEncoderArtifactError("artifact status must remain offline candidate")
        if self.renderer_version != RENDERER_VERSION or (
            self.renderer_sha256 != RENDERER_SHA256
        ):
            raise CommandEncoderArtifactError("artifact renderer is incompatible")
        if self.defer_label != DEFER_LABEL:
            raise CommandEncoderArtifactError("artifact defer label is incompatible")
        if self.encoder_dimension < 1 or self.candidate_limit < 1:
            raise CommandEncoderArtifactError("artifact dimensions and limits must be positive")
        if not 0.0 < self.target_precision <= 1.0:
            raise CommandEncoderArtifactError("target precision must be in (0, 1]")
        labels = tuple(target.label for target in self.targets)
        if not labels or len(labels) != len(set(labels)):
            raise CommandEncoderArtifactError("artifact targets must be non-empty and unique")
        calibration_labels = tuple(item.target_label for item in self.calibrations)
        if set(calibration_labels) != set(labels) or len(calibration_labels) != len(labels):
            raise CommandEncoderArtifactError("every target needs one calibration record")
        if any(
            item.enabled
            and (
                item.accepted_count == 0
                or item.precision_lower_bound < self.target_precision
            )
            for item in self.calibrations
        ):
            raise CommandEncoderArtifactError(
                "enabled target does not satisfy the precision lower-bound gate"
            )
        splits = tuple(item.split for item in self.datasets)
        if set(splits) != {"train", "calibration", "heldout"} or len(splits) != 3:
            raise CommandEncoderArtifactError("all three dataset splits are required")

    @property
    def target_by_label(self) -> dict[str, CommandEncoderTarget]:
        return {target.label: target for target in self.targets}

    @property
    def calibration_by_label(self) -> dict[str, CandidateCalibration]:
        return {item.target_label: item for item in self.calibrations}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "artifact_version": self.artifact_version,
            "registry_fingerprint": self.registry_fingerprint,
            "encoder": {
                "provider": self.encoder_provider,
                "model": self.encoder_model,
                "revision": self.encoder_revision,
                "sha256": self.encoder_sha256,
                "dimension": self.encoder_dimension,
                "profile_fingerprint": self.encoder_profile_fingerprint,
            },
            "renderer": {
                "version": self.renderer_version,
                "sha256": self.renderer_sha256,
            },
            "classifier": {
                "filename": self.classifier_filename,
                "sha256": self.classifier_sha256,
                "format": self.classifier_format,
            },
            "defer_label": self.defer_label,
            "candidate_limit": self.candidate_limit,
            "targets": [target.to_dict() for target in self.targets],
            "calibration": {
                "method": self.calibration_method,
                "version": self.calibration_version,
                "target_precision": self.target_precision,
                "candidates": [item.to_dict() for item in self.calibrations],
            },
            "datasets": [item.to_dict() for item in self.datasets],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommandEncoderArtifactManifest":
        try:
            encoder = value["encoder"]
            classifier = value["classifier"]
            calibration = value["calibration"]
            renderer = value["renderer"]
            return cls(
                schema_version=str(value["schema_version"]),
                status=str(value["status"]),
                artifact_version=str(value["artifact_version"]),
                registry_fingerprint=str(value["registry_fingerprint"]),
                encoder_provider=str(encoder["provider"]),
                encoder_model=str(encoder["model"]),
                encoder_revision=str(encoder["revision"]),
                encoder_sha256=str(encoder["sha256"]),
                encoder_dimension=int(encoder["dimension"]),
                encoder_profile_fingerprint=str(encoder["profile_fingerprint"]),
                renderer_version=str(renderer["version"]),
                renderer_sha256=str(renderer["sha256"]),
                classifier_filename=str(classifier["filename"]),
                classifier_sha256=str(classifier["sha256"]),
                classifier_format=str(classifier["format"]),
                defer_label=str(value["defer_label"]),
                candidate_limit=int(value["candidate_limit"]),
                calibration_method=str(calibration["method"]),
                calibration_version=str(calibration["version"]),
                target_precision=float(calibration["target_precision"]),
                targets=tuple(
                    CommandEncoderTarget.from_dict(item) for item in value["targets"]
                ),
                calibrations=tuple(
                    CandidateCalibration(
                        target_label=str(item["target_label"]),
                        threshold=float(item["threshold"]),
                        enabled=bool(item["enabled"]),
                        accepted_count=int(item["accepted_count"]),
                        correct_count=int(item["correct_count"]),
                        precision_lower_bound=float(item["precision_lower_bound"]),
                    )
                    for item in calibration["candidates"]
                ),
                datasets=tuple(
                    DatasetDigest(str(item["split"]), str(item["sha256"]))
                    for item in value["datasets"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, CommandEncoderArtifactError):
                raise
            raise CommandEncoderArtifactError("artifact manifest is invalid") from exc
