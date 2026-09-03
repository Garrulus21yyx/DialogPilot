import asyncio
import json
from pathlib import Path

import pytest

from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.structured_target_router import (
    CascadedTargetUnderstanding,
    StructuredTargetCommandRouter,
)
from application.target_encoder_artifact import (
    TargetEncoderArtifactError,
    load_target_text_encoder_artifact,
)
from application.target_encoder_understanding import TargetEncoderUnderstanding
from application.target_understanding import BoundedTargetUnderstanding
from application.turn_planning import ProposalDisposition
from evaluation.target_encoder_training import train_target_encoder


ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "target-encoder-zh-v1"


class _Provider:
    version = "target-encoder-fallback-test-v1"

    def __init__(self):
        self.calls = []

    async def route(self, payload):
        self.calls.append(payload)
        return {
            "status": "resolved",
            "goals": [{
                "kind": "order_status",
                "order_id": "DP2468",
            }],
        }


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


def _invoke(cascade, message):
    state = _state()
    observations = TurnObservations(message)
    deterministic = DeterministicResolver().resolve(observations, state)
    registry = build_default_capability_registry("tenant-a")
    return asyncio.run(cascade(observations, state, deterministic, registry))


def test_active_artifact_enables_only_class_that_passed_both_gates():
    artifact = load_target_text_encoder_artifact(ARTIFACT_DIR)

    assert artifact.manifest.threshold_by_skill == {
        "refund_status_summary": pytest.approx(0.4536190330982208),
    }
    assert artifact.manifest.classes[0].enabled is False
    assert artifact.manifest.classes[1].enabled is False
    assert artifact.manifest.classes[2].heldout_correct == 13
    assert artifact.manifest.classes[2].heldout_accepted == 13


def test_encoder_accepts_grounded_refund_status_and_skips_semantic_provider():
    artifact = load_target_text_encoder_artifact(ARTIFACT_DIR)
    provider = _Provider()
    cascade = CascadedTargetUnderstanding(
        BoundedTargetUnderstanding(),
        StructuredTargetCommandRouter(provider),
        encoder=TargetEncoderUnderstanding(artifact),
    )

    proposal = _invoke(cascade, "确认 RF3100 的退回进展")

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert proposal.reason_code == "ENCODER_FAST_PATH_ACCEPTED"
    assert proposal.commands[0].skill_id == "refund_status_summary"
    assert dict(
        (item.name, item.value) for item in proposal.commands[0].arguments
    ) == {"order_id": "RF3100"}
    assert provider.calls == []


def test_encoder_defers_generic_progress_and_write_language_to_semantic_router():
    artifact = load_target_text_encoder_artifact(ARTIFACT_DIR)
    provider = _Provider()
    cascade = CascadedTargetUnderstanding(
        BoundedTargetUnderstanding(),
        StructuredTargetCommandRouter(provider),
        encoder=TargetEncoderUnderstanding(artifact),
    )

    generic = _invoke(cascade, "帮我看看 DP2468 走到哪一步了")
    assert generic.reason_code == "STRUCTURED_SEMANTIC_ROUTER"
    assert len(provider.calls) == 1

    write = _invoke(cascade, "把 DP2468 直接退掉")
    assert write.reason_code == "STRUCTURED_SEMANTIC_ROUTER"
    assert len(provider.calls) == 2


def test_artifact_digest_tampering_fails_closed(tmp_path):
    manifest = json.loads((ARTIFACT_DIR / "manifest.json").read_text("utf-8"))
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "model.json").write_text("{}", encoding="utf-8")

    with pytest.raises(TargetEncoderArtifactError, match="digest"):
        load_target_text_encoder_artifact(tmp_path)


def test_training_reproduces_the_gated_artifact_contract(tmp_path):
    data = Path(__file__).resolve().parents[1] / "data" / "eval" / "target-encoder-zh-v1"
    manifest = train_target_encoder(
        train_path=data / "train.jsonl",
        calibration_path=data / "calibration.jsonl",
        heldout_path=data / "heldout.jsonl",
        output_dir=tmp_path,
    )
    artifact = load_target_text_encoder_artifact(tmp_path)

    assert manifest["heldout_summary"] == {
        "total": 100,
        "accepted": 13,
        "correct": 13,
        "accepted_precision": 1.0,
        "coverage": 0.13,
    }
    assert artifact.manifest.threshold_by_skill == {
        "refund_status_summary": pytest.approx(0.4536190330982208),
    }
