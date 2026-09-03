"""Explicit training-data boundary and exporter for command Encoder artifacts."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import joblib
import numpy as np
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression

from application.command_encoder_artifact import (
    DEFER_LABEL,
    SUPERVISION_SCHEMA,
    CandidateCalibration,
    CommandEncoderArtifactError,
    CommandEncoderArtifactManifest,
    CommandEncoderTarget,
    DatasetDigest,
)
from application.command_encoder_input import RENDERER_SHA256, RENDERER_VERSION
from application.hybrid_retrieval import EmbeddingProviderKind
from infrastructure.local_command_encoder_artifact import (
    CLASSIFIER_FORMAT,
    CommandEmbeddingProvider,
)


class CommandEncoderSupervisionError(ValueError):
    pass


ONE_SIDED_95_Z = 1.6448536269514722


@dataclass(frozen=True)
class ExplicitCommandExample:
    example_id: str
    rendered_input: str
    target_label: str


@dataclass(frozen=True)
class CommandEncoderExportConfig:
    artifact_version: str
    calibration_version: str
    target_precision: float = 0.99
    classifier_c: float = 1.0
    candidate_limit: int = 3

    def __post_init__(self) -> None:
        if not self.artifact_version.strip() or not self.calibration_version.strip():
            raise CommandEncoderArtifactError("export versions are required")
        if not 0.0 < self.target_precision <= 1.0:
            raise CommandEncoderArtifactError("target precision must be in (0, 1]")
        if self.classifier_c <= 0.0 or self.candidate_limit < 1:
            raise CommandEncoderArtifactError("classifier settings must be positive")


def load_explicit_command_supervision(path: Path) -> tuple[ExplicitCommandExample, ...]:
    """Read only exact command labels; route_mode/owner are never inferred."""
    examples: list[ExplicitCommandExample] = []
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError as exc:
        raise CommandEncoderSupervisionError("supervision dataset is unavailable") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if value.get("schema_version") != SUPERVISION_SCHEMA:
                raise CommandEncoderSupervisionError(
                    "explicit command/flow supervision is required; "
                    "route_mode or owner labels are not accepted"
                )
            if (
                value.get("renderer_version") != RENDERER_VERSION
                or value.get("renderer_sha256") != RENDERER_SHA256
            ):
                raise CommandEncoderSupervisionError(
                    "supervision renderer does not match the runtime renderer"
                )
            example = ExplicitCommandExample(
                example_id=str(value["example_id"]),
                rendered_input=str(value["rendered_input"]),
                target_label=str(value["target_label"]),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CommandEncoderSupervisionError(
                f"invalid supervision row at line {line_number}"
            ) from exc
        if not all((example.example_id.strip(), example.rendered_input.strip(), example.target_label.strip())):
            raise CommandEncoderSupervisionError(
                f"incomplete supervision row at line {line_number}"
            )
        examples.append(example)
    if not examples or len({item.example_id for item in examples}) != len(examples):
        raise CommandEncoderSupervisionError(
            "supervision examples must be non-empty and uniquely identified"
        )
    return tuple(examples)


def fit_and_export_command_encoder_artifact(
    *,
    train_path: Path,
    calibration_path: Path,
    heldout_path: Path,
    output_dir: Path,
    registry_fingerprint: str,
    targets: tuple[CommandEncoderTarget, ...],
    embedding_provider: CommandEmbeddingProvider,
    config: CommandEncoderExportConfig,
) -> CommandEncoderArtifactManifest:
    """Fit from explicit splits and export an offline-only candidate artifact."""
    train = load_explicit_command_supervision(train_path)
    calibration = load_explicit_command_supervision(calibration_path)
    heldout = load_explicit_command_supervision(heldout_path)
    if not targets or len({item.label for item in targets}) != len(targets):
        raise CommandEncoderArtifactError("command targets must be non-empty and unique")
    expected_labels = {DEFER_LABEL, *(item.label for item in targets)}
    for split_name, examples in (
        ("train", train),
        ("calibration", calibration),
        ("heldout", heldout),
    ):
        labels = {item.target_label for item in examples}
        if not labels <= expected_labels:
            raise CommandEncoderSupervisionError(
                f"{split_name} contains a target outside the frozen mapping"
            )
    if {item.target_label for item in train} != expected_labels or (
        {item.target_label for item in calibration} != expected_labels
    ):
        raise CommandEncoderSupervisionError(
            "train and calibration must cover every target plus __DEFER__"
        )
    calibration_counts = {
        label: sum(item.target_label == label for item in calibration)
        for label in expected_labels
    }
    if min(calibration_counts.values()) < 5:
        raise CommandEncoderSupervisionError(
            "calibration requires at least five examples per target"
        )

    profile = embedding_provider.profile
    if profile.provider_kind is not EmbeddingProviderKind.MODEL:
        raise CommandEncoderArtifactError(
            "command Encoder artifacts require a pinned model embedding profile"
        )
    train_vectors = _embed(embedding_provider, train, profile.dimension)
    calibration_vectors = _embed(
        embedding_provider, calibration, profile.dimension
    )
    base = LogisticRegression(
        C=config.classifier_c,
        class_weight="balanced",
        max_iter=2000,
        random_state=17,
    ).fit(train_vectors, [item.target_label for item in train])
    classifier = CalibratedClassifierCV(
        FrozenEstimator(base),
        method="sigmoid",
        cv=5,
    ).fit(calibration_vectors, [item.target_label for item in calibration])
    probabilities = np.asarray(
        classifier.predict_proba(calibration_vectors), dtype=np.float64
    )
    calibrations = tuple(
        _acceptance_for(
            target.label,
            tuple(str(item) for item in classifier.classes_),
            probabilities,
            tuple(item.target_label for item in calibration),
            config.target_precision,
        )
        for target in targets
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    classifier_path = output_dir / "classifier.joblib"
    manifest_path = output_dir / "manifest.json"
    if classifier_path.exists() or manifest_path.exists():
        raise CommandEncoderArtifactError("artifact output already exists")
    joblib.dump(classifier, classifier_path)
    manifest = CommandEncoderArtifactManifest(
        artifact_version=config.artifact_version,
        registry_fingerprint=registry_fingerprint,
        encoder_provider=profile.provider,
        encoder_model=profile.model,
        encoder_revision=profile.model_version,
        encoder_sha256=profile.model_digest,
        encoder_dimension=profile.dimension,
        encoder_profile_fingerprint=profile.fingerprint,
        classifier_filename=classifier_path.name,
        classifier_sha256=_file_sha256(classifier_path),
        classifier_format=CLASSIFIER_FORMAT,
        calibration_method="sklearn-sigmoid+wilson-one-sided-95",
        calibration_version=(
            f"{config.calibration_version}+scikit-learn-{sklearn.__version__}"
        ),
        target_precision=config.target_precision,
        targets=targets,
        calibrations=calibrations,
        datasets=(
            DatasetDigest("train", _file_sha256(train_path)),
            DatasetDigest("calibration", _file_sha256(calibration_path)),
            DatasetDigest("heldout", _file_sha256(heldout_path)),
        ),
        candidate_limit=config.candidate_limit,
    )
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _embed(
    provider: CommandEmbeddingProvider,
    examples: Sequence[ExplicitCommandExample],
    dimension: int,
) -> np.ndarray:
    vectors = np.asarray(
        provider.embed_queries(tuple(item.rendered_input for item in examples)),
        dtype=np.float32,
    )
    if vectors.shape != (len(examples), dimension):
        raise CommandEncoderArtifactError(
            "embedding output does not match the pinned profile"
        )
    return vectors


def _acceptance_for(
    target_label: str,
    classes: tuple[str, ...],
    probabilities: np.ndarray,
    expected: tuple[str, ...],
    target_precision: float,
) -> CandidateCalibration:
    target_index = classes.index(target_label)
    winners = probabilities.argmax(axis=1)
    scores = probabilities[:, target_index]
    thresholds = sorted(
        {
            float(scores[row])
            for row, winner in enumerate(winners)
            if int(winner) == target_index
        }
    )
    best_threshold: float | None = None
    best_accepted = -1
    best_correct = 0
    best_lower_bound = 0.0
    for threshold in thresholds:
        accepted = [
            row
            for row, winner in enumerate(winners)
            if int(winner) == target_index and float(scores[row]) >= threshold
        ]
        correct = sum(expected[row] == target_label for row in accepted)
        lower_bound = _wilson_lower_bound(correct, len(accepted))
        if accepted and lower_bound >= target_precision and (
            len(accepted) > best_accepted
        ):
            best_threshold = threshold
            best_accepted = len(accepted)
            best_correct = correct
            best_lower_bound = lower_bound
    return CandidateCalibration(
        target_label=target_label,
        threshold=best_threshold if best_threshold is not None else 1.0,
        enabled=best_threshold is not None,
        accepted_count=max(best_accepted, 0),
        correct_count=best_correct,
        precision_lower_bound=best_lower_bound,
    )


def _wilson_lower_bound(correct: int, total: int) -> float:
    """One-sided 95% Wilson score lower confidence bound."""
    if total == 0:
        return 0.0
    proportion = correct / total
    z_squared = ONE_SIDED_95_Z**2
    denominator = 1.0 + z_squared / total
    centre = proportion + z_squared / (2.0 * total)
    radius = ONE_SIDED_95_Z * math.sqrt(
        (proportion * (1.0 - proportion) + z_squared / (4.0 * total)) / total
    )
    return max(0.0, (centre - radius) / denominator)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
