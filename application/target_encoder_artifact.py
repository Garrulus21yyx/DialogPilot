"""Target-native, dependency-free runtime for a calibrated text encoder."""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from application.capability_registry import CapabilityEffect, CapabilityRegistryBundle
from application.encoder_fast_path import RankedCandidate


TARGET_ENCODER_SCHEMA = "dialogpilot-target-encoder-v2"
DEFER_LABEL = "__DEFER__"


class TargetEncoderArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class TargetEncoderClass:
    label: str
    owner_agent: str
    capability_kind: str
    capability_id: str
    required_arguments: tuple[str, ...]
    threshold: float
    enabled: bool
    calibration_accepted: int
    calibration_correct: int
    calibration_precision_lower_bound: float
    heldout_accepted: int
    heldout_correct: int
    heldout_precision_lower_bound: float

    @property
    def capability_ref(self) -> str:
        return f"{self.capability_kind}:{self.capability_id}"

    def __post_init__(self) -> None:
        if (
            not self.label.strip()
            or not self.owner_agent.strip()
            or self.capability_kind not in {"tool", "skill"}
            or not self.capability_id.strip()
        ):
            raise TargetEncoderArtifactError("encoder class identity is required")
        if len(self.required_arguments) != len(set(self.required_arguments)) or any(
            not item.strip() for item in self.required_arguments
        ):
            raise TargetEncoderArtifactError("encoder class arguments are invalid")
        if not 0.0 <= self.threshold <= 1.0:
            raise TargetEncoderArtifactError("encoder threshold must be in [0,1]")
        for accepted, correct in (
            (self.calibration_accepted, self.calibration_correct),
            (self.heldout_accepted, self.heldout_correct),
        ):
            if accepted < 0 or not 0 <= correct <= accepted:
                raise TargetEncoderArtifactError("encoder evaluation counts are invalid")
        if not all(0.0 <= value <= 1.0 for value in (
            self.calibration_precision_lower_bound,
            self.heldout_precision_lower_bound,
        )):
            raise TargetEncoderArtifactError("encoder precision bounds are invalid")


@dataclass(frozen=True)
class TargetEncoderManifest:
    artifact_version: str
    bundle_version: str
    model_filename: str
    model_sha256: str
    target_precision: float
    heldout_target_precision: float
    heldout_min_accepts: int
    classes: tuple[TargetEncoderClass, ...]
    dataset_sha256: tuple[tuple[str, str], ...]
    schema_version: str = TARGET_ENCODER_SCHEMA
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        if self.schema_version != TARGET_ENCODER_SCHEMA or self.status != "ACTIVE":
            raise TargetEncoderArtifactError("encoder artifact is not active Target v1")
        if not self.artifact_version.strip() or not self.bundle_version.strip():
            raise TargetEncoderArtifactError("encoder artifact identity is required")
        if Path(self.model_filename).name != self.model_filename:
            raise TargetEncoderArtifactError("encoder model filename must be local")
        _digest(self.model_sha256, "model")
        if not 0.0 < self.target_precision <= 1.0:
            raise TargetEncoderArtifactError("target precision must be in (0,1]")
        if not 0.0 < self.heldout_target_precision <= 1.0 or self.heldout_min_accepts < 1:
            raise TargetEncoderArtifactError("heldout acceptance gate is invalid")
        labels = tuple(item.label for item in self.classes)
        if not labels or len(labels) != len(set(labels)) or DEFER_LABEL in labels:
            raise TargetEncoderArtifactError("encoder target classes are invalid")
        if any(
            item.enabled and (
                item.calibration_accepted == 0
                or item.calibration_precision_lower_bound < self.target_precision
                or item.heldout_accepted < self.heldout_min_accepts
                or item.heldout_correct / item.heldout_accepted
                < self.heldout_target_precision
            )
            for item in self.classes
        ):
            raise TargetEncoderArtifactError("enabled class failed its precision gate")
        splits = dict(self.dataset_sha256)
        if set(splits) != {"train", "calibration", "heldout"}:
            raise TargetEncoderArtifactError("encoder artifact requires three data splits")
        for split, value in splits.items():
            _digest(value, split)

    @property
    def threshold_by_capability(self) -> dict[str, float]:
        return {
            item.capability_ref: item.threshold
            for item in self.classes
            if item.enabled
        }

    @property
    def required_arguments_by_capability(self) -> dict[str, tuple[str, ...]]:
        return {
            item.capability_ref: item.required_arguments
            for item in self.classes
            if item.enabled
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TargetEncoderManifest":
        try:
            return cls(
                artifact_version=str(value["artifact_version"]),
                bundle_version=str(value["bundle_version"]),
                model_filename=str(value["model_filename"]),
                model_sha256=str(value["model_sha256"]),
                target_precision=float(value["target_precision"]),
                heldout_target_precision=float(value["heldout_target_precision"]),
                heldout_min_accepts=int(value["heldout_min_accepts"]),
                classes=tuple(TargetEncoderClass(
                    **{
                        **item,
                        "required_arguments": tuple(item["required_arguments"]),
                    }
                ) for item in value["classes"]),
                dataset_sha256=tuple(
                    (str(item["split"]), str(item["sha256"]))
                    for item in value["datasets"]
                ),
                schema_version=str(value["schema_version"]),
                status=str(value["status"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, TargetEncoderArtifactError):
                raise
            raise TargetEncoderArtifactError("encoder manifest is invalid") from exc


class TargetTextEncoderArtifact:
    """Pure-Python hashed char n-gram logistic-regression inference."""

    def __init__(self, manifest: TargetEncoderManifest, model: Mapping[str, object]) -> None:
        self.manifest = manifest
        try:
            self._feature_count = int(model["feature_count"])
            self._ngram_min = int(model["ngram_min"])
            self._ngram_max = int(model["ngram_max"])
            self._classes = tuple(str(item) for item in model["classes"])
            self._coefficients = tuple(
                tuple(float(value) for value in row) for row in model["coefficients"]
            )
            self._intercepts = tuple(float(value) for value in model["intercepts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TargetEncoderArtifactError("encoder model is invalid") from exc
        expected = {DEFER_LABEL, *(item.label for item in manifest.classes)}
        if set(self._classes) != expected or len(self._classes) != len(expected):
            raise TargetEncoderArtifactError("encoder classes differ from manifest")
        if self._feature_count < 32 or not 1 <= self._ngram_min <= self._ngram_max:
            raise TargetEncoderArtifactError("encoder feature contract is invalid")
        if len(self._coefficients) != len(self._classes) or (
            len(self._intercepts) != len(self._classes)
        ) or any(len(row) != self._feature_count for row in self._coefficients):
            raise TargetEncoderArtifactError("encoder parameter dimensions are invalid")

    def validate_registry(self, registry: CapabilityRegistryBundle) -> None:
        if registry.bundle_version != self.manifest.bundle_version:
            raise TargetEncoderArtifactError("encoder bundle version is stale")
        for item in self.manifest.classes:
            owner = registry.agent(item.owner_agent)
            if item.capability_kind == "skill":
                capability = registry.skill(item.capability_id)
                valid_owner = capability.owner_agent == item.owner_agent
            else:
                capability = registry.tool(item.capability_id)
                valid_owner = capability.tool_id in owner.allowed_tool_ids
            if not valid_owner or capability.effect is not CapabilityEffect.READ:
                raise TargetEncoderArtifactError("encoder class capability binding is invalid")

    def predict(self, text: str) -> tuple[tuple[RankedCandidate, ...], float]:
        vector = hashed_char_ngram_vector(
            text,
            feature_count=self._feature_count,
            ngram_min=self._ngram_min,
            ngram_max=self._ngram_max,
        )
        logits = [
            intercept + sum(row[index] * value for index, value in vector.items())
            for row, intercept in zip(self._coefficients, self._intercepts, strict=True)
        ]
        maximum = max(logits)
        weights = [math.exp(value - maximum) for value in logits]
        total = sum(weights)
        scored = {
            label: weight / total
            for label, weight in zip(self._classes, weights, strict=True)
        }
        ranked = tuple(
            RankedCandidate(label, scored[label])
            for label in sorted(
                (item.label for item in self.manifest.classes),
                key=lambda label: (-scored[label], label),
            )
        )
        return ranked, scored[DEFER_LABEL]


def load_target_text_encoder_artifact(artifact_dir: Path) -> TargetTextEncoderArtifact:
    root = artifact_dir.resolve()
    try:
        manifest = TargetEncoderManifest.from_dict(json.loads(
            (root / "manifest.json").read_text("utf-8")
        ))
        model_path = root / manifest.model_filename
        if model_path.parent != root or _file_sha256(model_path) != manifest.model_sha256:
            raise TargetEncoderArtifactError("encoder model digest does not match")
        model = json.loads(model_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TargetEncoderArtifactError("encoder artifact is unavailable") from exc
    return TargetTextEncoderArtifact(manifest, model)


def hashed_char_ngram_vector(
    text: str,
    *,
    feature_count: int,
    ngram_min: int,
    ngram_max: int,
) -> dict[int, float]:
    normalized = "".join(
        unicodedata.normalize("NFKC", text).lower().split()
    )
    if not normalized:
        raise TargetEncoderArtifactError("encoder text must not be blank")
    counts: dict[int, float] = {}
    wrapped = f"^{normalized}$"
    for size in range(ngram_min, ngram_max + 1):
        for offset in range(max(0, len(wrapped) - size + 1)):
            token = wrapped[offset:offset + size].encode("utf-8")
            index = int.from_bytes(hashlib.sha256(token).digest()[:8], "big") % feature_count
            counts[index] = counts.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
    return {index: value / norm for index, value in counts.items()}


def _digest(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise TargetEncoderArtifactError(f"{label} digest must be lowercase SHA-256")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
