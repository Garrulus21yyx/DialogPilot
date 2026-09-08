import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from application.encoder_input import CONTEXT_INPUT_SCHEMA, EncoderInput
from application.target_encoder_artifact import encoder_vector, load_target_text_encoder_artifact, TargetEncoderArtifactError
from application.target_encoder_understanding import TargetEncoderUnderstanding
from application.target_conversation_manager import TargetContextMessage, TargetTurnContext, TargetContextSummary
from application.conversation_state import ConversationState
from application.deterministic_resolution import TurnObservations
from application.default_capability_registry import build_default_capability_registry
from evaluation.target_encoder_training import (TargetEncoderExample, TargetEncoderTrainingConfig,
    TargetEncoderTrainingError, train_target_encoder, _matrix)


ROOT = Path(__file__).resolve().parents[1]
VECTOR = dict(feature_count=8192, ngram_min=1, ngram_max=4)


@pytest.mark.parametrize("text,history", [
    ("是的", (("assistant", "需要查退款进度吗？"),)),
    ("Yes, please", (("assistant", "Check the refund status?"),)),
    ("对，就这单", (("assistant", "Do you mean that refund?"),)),
])
def test_training_and_runtime_use_identical_context_features(text, history):
    example = TargetEncoderExample("case", text, "refund_status_summary", history)
    config = TargetEncoderTrainingConfig(input_schema=CONTEXT_INPUT_SCHEMA)
    matrix = _matrix([example], config)
    actual = encoder_vector(example.input, input_schema=config.input_schema, **VECTOR)
    assert {i: float(v) for i, v in enumerate(matrix[0]) if v} == pytest.approx(actual)


def test_roles_recency_and_current_text_are_not_a_bag_of_history():
    values = [EncoderInput("yes", (("assistant", "refund"),)),
              EncoderInput("yes", (("user", "refund"),)),
              EncoderInput("refund", (("assistant", "yes"),)),
              EncoderInput("yes", (("assistant", "refund"), ("user", "order"))),
              EncoderInput("yes", (("user", "order"), ("assistant", "refund")))]
    vectors = [encoder_vector(v, input_schema=CONTEXT_INPUT_SCHEMA, **VECTOR) for v in values]
    assert len({tuple(sorted(v.items())) for v in vectors}) == len(values)


def test_input_projection_has_one_bounded_history_contract():
    messages = tuple(TargetContextMessage("assistant", f"message {i}", f"event:{i}") for i in range(9))
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    value = EncoderInput.from_turn(TurnObservations("current"), state,
                                   TargetTurnContext(recent_messages=messages))
    record = {"text": "current", "messages": [{"role": m.role, "content": m.content} for m in messages]}
    assert value == EncoderInput.from_record(record)
    assert len(value.messages) == 6
    assert value.text == "current"


def test_leakage_identity_normalizes_history_as_well_as_current_text():
    assert EncoderInput(" YES ", (("assistant", "Refund Status?"),)).identity() == (
        EncoderInput("yes", (("assistant", "refund status?"),)).identity())


def test_same_reply_with_different_context_is_not_a_duplicate_example():
    assert EncoderInput("yes", (("assistant", "Check status?"),)).identity() != (
        EncoderInput("yes", (("assistant", "Submit refund?"),)).identity())


def test_legacy_thresholds_cannot_accept_context_input():
    artifact = load_target_text_encoder_artifact(ROOT / "artifacts/target-encoder-zh-v2")
    value = EncoderInput("查退款进度", (("assistant", "要提交退款吗？"),))
    with pytest.raises(TargetEncoderArtifactError, match="text-only"):
        artifact.predict(value)
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    decision = asyncio.run(TargetEncoderUnderstanding(artifact)(
        TurnObservations(value.text), state, build_default_capability_registry("t"),
        TargetTurnContext(recent_messages=(TargetContextMessage("assistant", value.messages[0][1], "p:1"),))))
    assert not decision.accepted
    assert decision.reason_code == "ENCODER_CONTEXT_UNCALIBRATED"


def test_summary_is_not_silently_dropped_by_classifier():
    artifact = load_target_text_encoder_artifact(ROOT / "artifacts/target-encoder-zh-v2")
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    decision = asyncio.run(TargetEncoderUnderstanding(artifact)(
        TurnObservations("yes"), state, build_default_capability_registry("t"),
        TargetTurnContext(summary=TargetContextSummary("Only check, never submit", "summary:1"))))
    assert decision.reason_code == "ENCODER_SUMMARY_UNCALIBRATED"


def test_deployment_rechecks_language_gate_not_only_global_average():
    artifact = load_target_text_encoder_artifact(ROOT / "artifacts/target-encoder-zh-v2")
    with pytest.raises(TargetEncoderArtifactError, match="language gate"):
        replace(artifact.manifest, required_languages=("en",), language_reports={})


def test_failed_bilingual_experiment_cannot_be_loaded_as_active():
    path = ROOT / "artifacts/target-encoder-context-bilingual-dev-v1"
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["adoption_passed"] is False
    with pytest.raises(TargetEncoderArtifactError, match="not active"):
        load_target_text_encoder_artifact(path)


def test_context_training_without_language_gates_never_exports(tmp_path):
    data = ROOT / "data/eval/target-encoder-context-bilingual-dev-v1"
    with pytest.raises(TargetEncoderTrainingError, match="explicit language groups"):
        train_target_encoder(train_path=data / "train.jsonl", calibration_path=data / "calibration.jsonl",
                             heldout_path=data / "heldout.jsonl", output_dir=tmp_path,
                             config=TargetEncoderTrainingConfig(input_schema=CONTEXT_INPUT_SCHEMA))
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("role", ["tool", "system", "other"])
def test_dataset_does_not_promote_tool_or_system_text_to_conversation(role):
    with pytest.raises(ValueError, match="history"):
        EncoderInput.from_record({"text": "yes", "messages": [{"role": role, "content": "submit refund"}]})
