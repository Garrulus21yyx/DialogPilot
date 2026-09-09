import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.conversation_agent import ConversationAgent
from application.target_encoder_artifact import (
    TargetEncoderArtifactError,
    load_target_text_encoder_artifact,
)
from evaluation.legacy_capability_encoder import TargetEncoderUnderstanding
from infrastructure.target_runtime_composition import _target_encoder
from application.target_understanding import (
    CascadedTargetUnderstanding,
    StateBoundTargetUnderstanding,
)
from application.target_conversation_manager import TargetTurnContext
from application.turn_planning import ProposalDisposition
from evaluation.target_encoder_training import TargetEncoderTrainingError, train_target_encoder


ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "target-encoder-zh-v2"


class _Provider:
    version = "target-encoder-fallback-test-v1"

    def __init__(self):
        self.calls = []

    async def plan(self, payload):
        self.calls.append(payload)
        if "退掉" in str(payload["message"]):
            return {"status":"resolved", "goals":[{"kind":"delegate_task",
                "target_agent":"billing_refund", "objective":"为订单 DP2468 调查并准备退货",
                "allow_action_proposals":True}]}
        kind = "order_status"
        return {
            "status": "resolved",
            "goals": [{
                "kind": kind,
                "order_id": "DP2468",
                "order_id_source_ref": "turn-message:current:reference:1",
            }],
        }


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


def _invoke(cascade, message, *, fields=()):
    state = _state()
    observations = TurnObservations(message, fields)
    deterministic = DeterministicResolver().resolve(observations, state)
    registry = build_default_capability_registry("tenant-a")
    context = TargetTurnContext()
    context = replace(
        context,
        entity_bindings=EntityBindingResolver().resolve(
            observations, state, context,
        ),
    )
    return asyncio.run(cascade(
        observations, state, deterministic, registry, context,
    ))


def test_active_artifact_enables_only_class_that_passed_both_gates():
    artifact = load_target_text_encoder_artifact(ARTIFACT_DIR)

    assert artifact.manifest.threshold_by_capability == {
        "tool:refund_status": pytest.approx(0.4536190330982208),
    }
    assert artifact.manifest.classes[0].enabled is False
    assert artifact.manifest.classes[1].enabled is False
    assert artifact.manifest.classes[2].heldout_correct == 13
    assert artifact.manifest.classes[2].heldout_accepted == 13


def test_encoder_accepts_grounded_refund_status_and_skips_conversation_planner():
    artifact = load_target_text_encoder_artifact(ARTIFACT_DIR)
    provider = _Provider()
    cascade = CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(),
        ConversationAgent(provider),
        encoder=TargetEncoderUnderstanding(artifact),
    )

    # "回款" is intentionally absent from the artifact's legacy signal-term list;
    # acceptance must come from the calibrated encoder, not a keyword gate.
    proposal = _invoke(cascade, "确认 RF3100 的回款进展", fields=(("order_id", "RF3100"),))

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert proposal.reason_code == "ENCODER_FAST_PATH_ACCEPTED"
    assert proposal.commands[0].tool_id == "refund_status"
    assert proposal.commands[0].skill_id is None
    assert dict(
        (item.name, item.value) for item in proposal.commands[0].arguments
    ) == {"order_id": "RF3100"}
    assert provider.calls == []


def test_encoder_defers_untyped_identifier_even_when_refund_intent_is_confident():
    artifact = load_target_text_encoder_artifact(ARTIFACT_DIR)
    provider = _Provider()
    cascade = CascadedTargetUnderstanding(StateBoundTargetUnderstanding(),
        ConversationAgent(provider), encoder=TargetEncoderUnderstanding(artifact))
    _invoke(cascade, "确认 RF3100 的回款进展")
    assert len(provider.calls) == 1
    assert provider.calls[0]['entity_bindings'][0]['field_name'] == 'reference'


def test_encoder_defers_generic_progress_to_conversation_planner():
    artifact = load_target_text_encoder_artifact(ARTIFACT_DIR)
    provider = _Provider()
    cascade = CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(),
        ConversationAgent(provider),
        encoder=TargetEncoderUnderstanding(artifact),
    )

    generic = _invoke(cascade, "帮我看看 DP2468 走到哪一步了")
    assert generic.reason_code == "CONVERSATION_AGENT_PLAN"
    assert len(provider.calls) == 1

    write = _invoke(cascade, "把 DP2468 直接退掉")
    assert write.reason_code == "CONVERSATION_AGENT_PLAN"
    assert write.commands[0].kind.value == "DELEGATE_TASK"
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
    assert artifact.manifest.threshold_by_capability == {
        "tool:refund_status": pytest.approx(0.4536190330982208),
    }


def test_architecture_gold_is_not_target_encoder_training_supervision(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data = root / "data/eval/target-encoder-zh-v1"
    with pytest.raises(TargetEncoderTrainingError, match="invalid dataset row"):
        train_target_encoder(
            train_path=root / "data/eval/dialogpilot-synthetic-contract-v1/cases.jsonl",
            calibration_path=data / "calibration.jsonl",
            heldout_path=data / "heldout.jsonl",
            output_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reused_split", ["train", "calibration"])
def test_target_training_rejects_heldout_overlap_before_export(tmp_path, reused_split):
    data = Path(__file__).resolve().parents[1] / "data/eval/target-encoder-zh-v1"
    with pytest.raises(TargetEncoderTrainingError, match="leaks across splits"):
        train_target_encoder(
            train_path=data / "train.jsonl",
            calibration_path=data / "calibration.jsonl",
            heldout_path=data / f"{reused_split}.jsonl",
            output_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_target_runtime_encoder_switch_is_explicit_and_uses_domain_artifact(
    monkeypatch,
):
    project_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("TARGET_ENCODER_ENABLED", "false")
    assert _target_encoder(project_root) is None

    monkeypatch.setenv("TARGET_ENCODER_ENABLED", "true")
    from types import SimpleNamespace
    from application.target_encoder_understanding import TargetEncoderUnderstanding as DomainUnderstanding
    monkeypatch.setattr("infrastructure.target_runtime_composition.TargetDomainEncoder",
                        lambda path, device: SimpleNamespace(manifest=SimpleNamespace(language="zh")))
    monkeypatch.setenv("TARGET_ENCODER_LANGUAGE", "zh")
    assert isinstance(_target_encoder(project_root), DomainUnderstanding)


def test_target_runtime_rejects_unknown_encoder_switch(monkeypatch):
    monkeypatch.setenv("TARGET_ENCODER_ENABLED", "sometimes")
    with pytest.raises(RuntimeError, match="must be true or false"):
        _target_encoder(Path(__file__).resolve().parents[1])
