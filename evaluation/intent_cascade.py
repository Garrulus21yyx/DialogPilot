"""Selective Encoder-to-LLM cascade calibration and replay."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from evaluation.local_encoder_classifier import classification_metrics


def stratified_group_folds(
    labels: Sequence[str],
    group_ids: Sequence[str],
    *,
    folds: int = 5,
    salt: str = "encoder-llm-cascade-v1",
) -> np.ndarray:
    if len(labels) != len(group_ids):
        raise ValueError("labels and group_ids must have equal length")
    if folds < 2:
        raise ValueError("folds must be at least two")
    group_labels: dict[str, set[str]] = defaultdict(set)
    for label, group_id in zip(labels, group_ids):
        group_labels[str(group_id)].add(str(label))
    crossed = [group_id for group_id, values in group_labels.items() if len(values) != 1]
    if crossed:
        raise ValueError(f"groups cross labels: {crossed[:5]}")
    by_label: dict[str, list[str]] = defaultdict(list)
    for group_id, values in group_labels.items():
        by_label[next(iter(values))].append(group_id)
    assignments: dict[str, int] = {}
    for label, groups in by_label.items():
        ordered = sorted(
            groups,
            key=lambda group_id: hashlib.sha256(
                f"{salt}:{label}:{group_id}".encode("utf-8")
            ).hexdigest(),
        )
        for index, group_id in enumerate(ordered):
            assignments[group_id] = index % folds
    return np.asarray([assignments[str(group_id)] for group_id in group_ids], dtype=int)


def risk_coverage_point(
    expected: Sequence[str],
    predicted: Sequence[str],
    confidence: Sequence[float],
    threshold: float,
) -> dict[str, Any]:
    accepted = [float(score) >= float(threshold) for score in confidence]
    accepted_count = sum(accepted)
    correct = sum(
        take and gold == guess
        for gold, guess, take in zip(expected, predicted, accepted)
    )
    return {
        "threshold": round(float(threshold), 12),
        "accepted_count": accepted_count,
        "fallback_count": len(expected) - accepted_count,
        "coverage": round(accepted_count / len(expected), 6) if expected else 0.0,
        "accepted_accuracy": (
            round(correct / accepted_count, 6) if accepted_count else None
        ),
        "accepted_errors": accepted_count - correct,
    }


def select_threshold_for_precision(
    expected: Sequence[str],
    predicted: Sequence[str],
    confidence: Sequence[float],
    *,
    target_accuracy: float,
) -> dict[str, Any]:
    if not expected or len(expected) != len(predicted) or len(expected) != len(confidence):
        raise ValueError("expected, predicted, and confidence must be non-empty and aligned")
    thresholds = sorted({0.0, *(float(score) for score in confidence)})
    eligible = []
    for threshold in thresholds:
        point = risk_coverage_point(expected, predicted, confidence, threshold)
        accuracy = point["accepted_accuracy"]
        if accuracy is not None and accuracy >= target_accuracy:
            eligible.append(point)
    if not eligible:
        raise RuntimeError(f"no non-empty coverage reaches accuracy {target_accuracy}")
    return min(
        eligible,
        key=lambda point: (
            -float(point["coverage"]),
            float(point["threshold"]),
        ),
    )


def replay_cascade(
    expected: Sequence[str],
    encoder_predictions: Sequence[str],
    encoder_confidence: Sequence[float],
    llm_outputs: Sequence[Mapping[str, Any]],
    *,
    threshold: float,
) -> dict[str, Any]:
    if not (
        len(expected)
        == len(encoder_predictions)
        == len(encoder_confidence)
        == len(llm_outputs)
    ):
        raise ValueError("cascade inputs must be aligned")
    predictions: list[str] = []
    confidences: list[float] = []
    routes: list[str] = []
    for encoder_intent, encoder_score, llm in zip(
        encoder_predictions, encoder_confidence, llm_outputs
    ):
        if float(encoder_score) >= float(threshold):
            predictions.append(str(encoder_intent))
            confidences.append(float(encoder_score))
            routes.append("encoder")
        else:
            predictions.append(str(llm.get("intent") or "other"))
            confidences.append(float(llm.get("confidence", 0.0) or 0.0))
            routes.append("llm")
    metrics = classification_metrics(expected, predictions, confidences)
    encoder_count = routes.count("encoder")
    return {
        "threshold": round(float(threshold), 12),
        "metrics": metrics,
        "routes": {
            "encoder": encoder_count,
            "llm": len(routes) - encoder_count,
            "encoder_fraction": round(encoder_count / len(routes), 6) if routes else 0.0,
            "llm_fallback_fraction": (
                round((len(routes) - encoder_count) / len(routes), 6) if routes else 0.0
            ),
        },
        "predictions": predictions,
        "confidences": confidences,
        "route_by_index": routes,
    }
