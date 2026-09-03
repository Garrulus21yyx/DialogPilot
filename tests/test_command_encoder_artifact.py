from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk
from application.command_encoder_artifact import (
    DEFER_LABEL,
    SUPERVISION_SCHEMA,
    CommandEncoderArtifactError,
    CommandEncoderTarget,
)
from application.command_encoder_input import (
    RENDERER_SHA256,
    RENDERER_VERSION,
    render_command_encoder_input,
)
from application.hybrid_retrieval import EmbeddingProfile, EmbeddingProviderKind
from application.route_policy_v2 import (
    ActionDefinition,
    ApprovalPolicy,
    FlowActionRegistry,
    FlowDefinition,
    WorkKind,
)
from application.selective_command_producer import EncoderDisposition
from application.turn_state import (
    ActiveFlowRef,
    FlowAggregateVersion,
    FlowDefinitionRef,
    PrincipalScope,
    StateAvailability,
    StateSourceStatus,
    TurnStateSnapshot,
)
from application.turn_understanding import CommandKind
from evaluation.command_encoder_artifact_export import (
    CommandEncoderExportConfig,
    CommandEncoderSupervisionError,
    fit_and_export_command_encoder_artifact,
    load_explicit_command_supervision,
)
from infrastructure.bge_m3_embedding import (
    BGE_M3_DIMENSION,
    BGE_M3_MODEL_ID,
    BGE_M3_PREPROCESSING,
    BGE_M3_PROVIDER_ID,
)
from infrastructure.local_command_encoder_artifact import (
    load_local_command_encoder_artifact,
)


REFUND_STATUS = FlowDefinitionRef("refund_status", "v1")


class FakeBGEProvider:
    profile = EmbeddingProfile(
        provider=BGE_M3_PROVIDER_ID,
        provider_kind=EmbeddingProviderKind.MODEL,
        model=BGE_M3_MODEL_ID,
        model_version="bge-revision-1",
        dimension=BGE_M3_DIMENSION,
        model_digest="a" * 64,
        document_preprocessing=BGE_M3_PREPROCESSING,
        query_preprocessing=BGE_M3_PREPROCESSING,
    )

    def embed_queries(self, texts):
        vectors = []
        for text in texts:
            message = json.loads(text)["message"]
            leading = (1.0, 0.0) if message.startswith("refund") else (0.0, 1.0)
            vectors.append([*leading, *([0.0] * (BGE_M3_DIMENSION - 2))])
        return vectors


def _state() -> TurnStateSnapshot:
    principal = PrincipalScope("tenant-1", "user-1", "conversation-1")
    return TurnStateSnapshot(
        "request-1",
        principal,
        FlowAggregateVersion("flow-state:conversation-1", 1),
        (ActiveFlowRef(
            REFUND_STATUS,
            "refund-instance-1",
            1,
            principal.fingerprint,
        ),),
        None,
        (),
        (),
        (),
        (StateSourceStatus(
            "flow_state",
            StateAvailability.CURRENT,
            "flow-state-v1",
        ),),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )


def _registry(*, generation: str = "flow-registry-v1") -> FlowActionRegistry:
    return FlowActionRegistry(
        "tenant-1",
        generation,
        (FlowDefinition(REFUND_STATUS, (CommandKind.CONTINUE_FLOW,)),),
        (ActionDefinition(
            "refund.status.read",
            "v1",
            CommandKind.CONTINUE_FLOW,
            REFUND_STATUS,
            WorkKind.AGENT,
            AgentType.BILLING,
            TaskEffect.READ_ONLY,
            TaskRisk.LOW,
            ("refund.current_state",),
            ("refund_status",),
            ApprovalPolicy.USER_COMMAND_SUFFICIENT,
            "read the current refund status",
        ),),
    )


def _write_supervision(path, messages_and_labels) -> None:
    state = _state()
    rows = [
        {
            "schema_version": SUPERVISION_SCHEMA,
            "example_id": f"example-{index}",
            "renderer_version": RENDERER_VERSION,
            "renderer_sha256": RENDERER_SHA256,
            "rendered_input": render_command_encoder_input(message, state),
            "target_label": label,
        }
        for index, (message, label) in enumerate(messages_and_labels)
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _artifact(tmp_path):
    target = CommandEncoderTarget(CommandKind.CONTINUE_FLOW, REFUND_STATUS)
    train_path = tmp_path / "train.jsonl"
    calibration_path = tmp_path / "calibration.jsonl"
    heldout_path = tmp_path / "heldout.jsonl"
    _write_supervision(
        train_path,
        tuple((f"refund status {index}", target.label) for index in range(8))
        + tuple((f"unclear request {index}", DEFER_LABEL) for index in range(8)),
    )
    _write_supervision(
        calibration_path,
        tuple((f"refund calibration {index}", target.label) for index in range(6))
        + tuple((f"unclear calibration {index}", DEFER_LABEL) for index in range(6)),
    )
    _write_supervision(
        heldout_path,
        (("refund heldout", target.label), ("unclear heldout", DEFER_LABEL)),
    )
    registry = _registry()
    artifact_dir = tmp_path / "artifact"
    manifest = fit_and_export_command_encoder_artifact(
        train_path=train_path,
        calibration_path=calibration_path,
        heldout_path=heldout_path,
        output_dir=artifact_dir,
        registry_fingerprint=registry.fingerprint,
        targets=(target,),
        embedding_provider=FakeBGEProvider(),
        config=CommandEncoderExportConfig(
            "command-encoder-test-v1",
            "sigmoid-test-v1",
            target_precision=0.65,
        ),
    )
    return manifest, artifact_dir, registry, target


def test_explicit_artifact_round_trip_produces_registry_candidate_and_defer(
    tmp_path,
) -> None:
    manifest, artifact_dir, registry, target = _artifact(tmp_path)
    artifact = load_local_command_encoder_artifact(
        artifact_dir, FakeBGEProvider()
    )

    accepted = asyncio.run(artifact.decide("refund status", _state(), registry))
    deferred = asyncio.run(artifact.decide("unclear request", _state(), registry))

    assert accepted.disposition is EncoderDisposition.ACCEPT
    assert accepted.selected is not None
    assert accepted.selected.command_kind is CommandKind.CONTINUE_FLOW
    assert accepted.selected.flow == REFUND_STATUS
    assert deferred.disposition is EncoderDisposition.DEFER
    assert manifest.status == "OFFLINE_CANDIDATE"
    assert manifest.encoder_revision == "bge-revision-1"
    assert manifest.encoder_sha256 == "a" * 64
    assert manifest.registry_fingerprint == registry.fingerprint
    assert manifest.target_by_label[target.label] == target
    target_calibration = manifest.calibration_by_label[target.label]
    assert target_calibration.enabled is True
    assert target_calibration.accepted_count == 6
    assert target_calibration.correct_count == 6
    assert target_calibration.precision_lower_bound >= 0.65
    assert {item.split for item in manifest.datasets} == {
        "train",
        "calibration",
        "heldout",
    }


def test_artifact_refuses_a_different_registry(tmp_path) -> None:
    _, artifact_dir, _, _ = _artifact(tmp_path)
    artifact = load_local_command_encoder_artifact(
        artifact_dir, FakeBGEProvider()
    )

    with pytest.raises(CommandEncoderArtifactError, match="current Registry"):
        asyncio.run(artifact.decide(
            "refund status",
            _state(),
            _registry(generation="flow-registry-v2"),
        ))


def test_locked_architecture_contract_is_not_command_training_supervision() -> None:
    with pytest.raises(
        CommandEncoderSupervisionError,
        match="explicit command/flow supervision is required",
    ):
        load_explicit_command_supervision(
            Path(__file__).parents[1]
            / "data/eval/dialogpilot-synthetic-contract-v1/cases.jsonl"
        )
