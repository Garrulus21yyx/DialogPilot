"""Load and evaluate a local command Encoder artifact."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Protocol, Sequence

import joblib
import numpy as np

from application.command_encoder_artifact import (
    DEFER_LABEL,
    CommandEncoderArtifactError,
    CommandEncoderArtifactManifest,
)
from application.command_encoder_input import render_command_encoder_input
from application.hybrid_retrieval import EmbeddingProfile, EmbeddingProviderKind
from application.route_policy_v2 import FlowActionRegistry
from application.selective_command_producer import (
    EncoderCommandCandidate,
    EncoderCommandDecision,
    EncoderDisposition,
)
from application.turn_state import TurnStateSnapshot


CLASSIFIER_FORMAT = "sklearn-calibrated-predict-proba-joblib-v1"


class CommandEncoderArtifactUnavailable(RuntimeError):
    pass


class CommandEmbeddingProvider(Protocol):
    profile: EmbeddingProfile

    def embed_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class ProbabilityClassifier(Protocol):
    classes_: Sequence[str]

    def predict_proba(self, values: np.ndarray) -> np.ndarray: ...


class LocalCommandEncoderArtifact:
    """Offline candidate artifact; SelectiveCommandProducer owns fast-path use."""

    def __init__(
        self,
        manifest: CommandEncoderArtifactManifest,
        classifier: ProbabilityClassifier,
        embedding_provider: CommandEmbeddingProvider,
    ) -> None:
        self.manifest = manifest
        self._classifier = classifier
        self._embedding_provider = embedding_provider
        self._classes = tuple(str(item) for item in classifier.classes_)
        expected_classes = {DEFER_LABEL, *manifest.target_by_label}
        if set(self._classes) != expected_classes or len(self._classes) != len(
            expected_classes
        ):
            raise CommandEncoderArtifactError(
                "classifier classes do not match manifest targets"
            )

    async def decide(
        self,
        message: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        *,
        history: tuple[Mapping[str, str], ...] = (),
    ) -> EncoderCommandDecision:
        if registry.fingerprint != self.manifest.registry_fingerprint:
            raise CommandEncoderArtifactError(
                "command Encoder artifact does not match the current Registry"
            )
        registered_targets = {
            (action.command_kind, action.flow) for action in registry.actions
        }
        if any(
            (target.command_kind, target.flow) not in registered_targets
            for target in self.manifest.targets
        ):
            raise CommandEncoderArtifactError(
                "command Encoder target mapping is absent from the current Registry"
            )
        rendered = render_command_encoder_input(message, state, history=history)
        vectors = np.asarray(
            self._embedding_provider.embed_queries((rendered,)),
            dtype=np.float32,
        )
        if vectors.shape != (1, self.manifest.encoder_dimension):
            raise CommandEncoderArtifactUnavailable(
                "command Encoder returned an incompatible embedding"
            )
        probabilities = np.asarray(
            self._classifier.predict_proba(vectors),
            dtype=np.float64,
        )
        if probabilities.shape != (1, len(self._classes)):
            raise CommandEncoderArtifactUnavailable(
                "command classifier returned an incompatible probability vector"
            )
        scored = dict(zip(self._classes, probabilities[0], strict=True))
        target_by_label = self.manifest.target_by_label
        ranked_labels = sorted(
            target_by_label,
            key=lambda label: (-float(scored[label]), label),
        )[: self.manifest.candidate_limit]
        candidates = tuple(
            EncoderCommandCandidate(
                candidate_id=label,
                command_kind=target_by_label[label].command_kind,
                flow=target_by_label[label].flow,
                calibrated_accept_probability=float(scored[label]),
            )
            for label in ranked_labels
        )

        winner = max(self._classes, key=lambda label: float(scored[label]))
        calibration = self.manifest.calibration_by_label.get(winner)
        accepted = (
            winner != DEFER_LABEL
            and calibration is not None
            and calibration.enabled
            and float(scored[winner]) >= calibration.threshold
        )
        return EncoderCommandDecision(
            disposition=(
                EncoderDisposition.ACCEPT if accepted else EncoderDisposition.DEFER
            ),
            candidates=candidates,
            selected_candidate_id=winner if accepted else None,
            artifact_version=self.manifest.artifact_version,
            calibration_version=self.manifest.calibration_version,
            reason_code=(
                "CALIBRATED_ACCEPT"
                if accepted
                else "MODEL_DEFER" if winner == DEFER_LABEL else "BELOW_ACCEPT_THRESHOLD"
            ),
        )


def load_local_command_encoder_artifact(
    artifact_dir: Path,
    embedding_provider: CommandEmbeddingProvider,
) -> LocalCommandEncoderArtifact:
    """Load a locally provisioned artifact after identity and digest checks."""
    root = artifact_dir.resolve()
    manifest_path = root / "manifest.json"
    try:
        manifest = CommandEncoderArtifactManifest.from_dict(
            json.loads(manifest_path.read_text("utf-8"))
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandEncoderArtifactUnavailable(
            "command Encoder manifest is unavailable"
        ) from exc
    if manifest.classifier_format != CLASSIFIER_FORMAT:
        raise CommandEncoderArtifactError("unsupported command classifier format")
    classifier_path = root / manifest.classifier_filename
    if classifier_path.parent != root:
        raise CommandEncoderArtifactError("classifier path must stay in artifact directory")
    try:
        actual_digest = _file_sha256(classifier_path)
    except OSError as exc:
        raise CommandEncoderArtifactUnavailable(
            "command classifier is unavailable"
        ) from exc
    if actual_digest != manifest.classifier_sha256:
        raise CommandEncoderArtifactError("command classifier digest does not match")
    _validate_embedding_identity(manifest, embedding_provider.profile)
    try:
        classifier = joblib.load(classifier_path)
    except Exception as exc:
        raise CommandEncoderArtifactUnavailable(
            "command classifier could not be loaded"
        ) from exc
    return LocalCommandEncoderArtifact(manifest, classifier, embedding_provider)


def _validate_embedding_identity(
    manifest: CommandEncoderArtifactManifest,
    profile: EmbeddingProfile,
) -> None:
    actual = (
        profile.provider,
        profile.model,
        profile.model_version,
        profile.model_digest,
        profile.dimension,
        profile.fingerprint,
    )
    expected = (
        manifest.encoder_provider,
        manifest.encoder_model,
        manifest.encoder_revision,
        manifest.encoder_sha256,
        manifest.encoder_dimension,
        manifest.encoder_profile_fingerprint,
    )
    if profile.provider_kind is not EmbeddingProviderKind.MODEL or actual != expected:
        raise CommandEncoderArtifactError(
            "command Encoder embedding identity does not match the artifact"
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
