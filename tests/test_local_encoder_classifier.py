import pytest

pytest.importorskip("sklearn", reason="optional semantic-intent runtime is not installed")

import numpy as np

from evaluation.local_encoder_classifier import (
    EncoderClassifierConfig,
    classification_metrics,
    deterministic_train_validation_split,
    fit_model,
    render_case_text,
)


def test_render_case_text_keeps_history_before_current_message():
    case = {
        "input": {
            "history": [{"role": "assistant", "content": "需要转人工吗？"}],
            "message": "需要",
        }
    }
    assert render_case_text(case) == "assistant: 需要转人工吗？\nuser: 需要"


def test_deterministic_split_preserves_every_label():
    labels = [label for label in ("a", "b", "other") for _ in range(10)]
    first = deterministic_train_validation_split(labels)
    second = deterministic_train_validation_split(labels)
    assert all(np.array_equal(left, right) for left, right in zip(first, second))
    for indices in first:
        assert {labels[index] for index in indices} == {"a", "b", "other"}


def test_joint_classifier_learns_separable_intents():
    embeddings = np.asarray(
        [[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9], [-1, -1], [-0.9, -1]],
        dtype=np.float32,
    )
    labels = ["account", "account", "refund", "refund", "other", "other"]
    model = fit_model(embeddings, labels, EncoderClassifierConfig("joint", 10.0))
    predicted, confidence = model.predict(embeddings)
    assert predicted == labels
    assert all(0.0 <= value <= 1.0 for value in confidence)


def test_cascade_owns_oos_decision_separately_from_known_classifier():
    embeddings = np.asarray(
        [[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9], [-1, -1], [-0.9, -1]],
        dtype=np.float32,
    )
    labels = ["account", "account", "refund", "refund", "other", "other"]
    config = EncoderClassifierConfig("cascade", 10.0, 10.0, 0.5)
    model = fit_model(embeddings, labels, config)
    predicted, _ = model.predict(embeddings)
    assert predicted == labels


def test_metrics_report_oos_separately():
    metrics = classification_metrics(
        ["account", "other", "other"],
        ["account", "account", "other"],
        [0.9, 0.6, 0.8],
    )
    assert metrics["accuracy"] == pytest.approx(2 / 3, abs=1e-6)
    assert metrics["oos_recall"] == 0.5
    assert "confidence_ece_10" in metrics
