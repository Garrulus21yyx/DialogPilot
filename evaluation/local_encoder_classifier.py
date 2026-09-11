"""Leakage-safe frozen-encoder intent-classification experiment utilities."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from sklearn.model_selection import train_test_split


OOS_LABEL = "other"


@dataclass(frozen=True)
class EncoderClassifierConfig:
    architecture: str
    classifier_c: float
    oos_c: float | None = None
    oos_threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FrozenEncoderIntentModel:
    config: EncoderClassifierConfig
    joint_classifier: LogisticRegression | None = None
    oos_classifier: LogisticRegression | None = None
    known_classifier: LogisticRegression | None = None

    def predict(self, embeddings: np.ndarray) -> tuple[list[str], list[float]]:
        matrix = np.asarray(embeddings, dtype=np.float32)
        if self.config.architecture == "joint":
            if self.joint_classifier is None:
                raise RuntimeError("joint classifier is not fitted")
            probabilities = self.joint_classifier.predict_proba(matrix)
            winners = probabilities.argmax(axis=1)
            return (
                [str(self.joint_classifier.classes_[index]) for index in winners],
                [float(probabilities[row, index]) for row, index in enumerate(winners)],
            )

        if self.config.architecture != "cascade":
            raise ValueError(f"unsupported architecture: {self.config.architecture}")
        if self.oos_classifier is None or self.known_classifier is None:
            raise RuntimeError("cascade classifiers are not fitted")
        threshold = float(self.config.oos_threshold)
        oos_probabilities = self.oos_classifier.predict_proba(matrix)
        oos_index = list(self.oos_classifier.classes_).index(True)
        p_oos = oos_probabilities[:, oos_index]
        known_probabilities = self.known_classifier.predict_proba(matrix)
        known_winners = known_probabilities.argmax(axis=1)
        predictions: list[str] = []
        confidences: list[float] = []
        for row, known_index in enumerate(known_winners):
            if float(p_oos[row]) >= threshold:
                predictions.append(OOS_LABEL)
                confidences.append(float(p_oos[row]))
            else:
                predictions.append(str(self.known_classifier.classes_[known_index]))
                confidences.append(
                    float((1.0 - p_oos[row]) * known_probabilities[row, known_index])
                )
        return predictions, confidences


def render_case_text(case: Mapping[str, Any]) -> str:
    """Render current utterance plus explicit dialogue context without label metadata."""
    payload = case.get("input", {})
    history = payload.get("history") or []
    parts = [f"{item.get('role', 'unknown')}: {item.get('content', '')}" for item in history]
    parts.append(f"user: {payload.get('message', '')}")
    return "\n".join(parts)


def fit_model(
    embeddings: np.ndarray,
    labels: Sequence[str],
    config: EncoderClassifierConfig,
) -> FrozenEncoderIntentModel:
    matrix = np.asarray(embeddings, dtype=np.float32)
    targets = np.asarray([str(label) for label in labels])
    common = {"class_weight": "balanced", "max_iter": 2000, "random_state": 17}
    if config.architecture == "joint":
        classifier = LogisticRegression(C=config.classifier_c, **common).fit(matrix, targets)
        return FrozenEncoderIntentModel(config=config, joint_classifier=classifier)
    if config.architecture != "cascade":
        raise ValueError(f"unsupported architecture: {config.architecture}")
    if config.oos_c is None or config.oos_threshold is None:
        raise ValueError("cascade requires oos_c and oos_threshold")
    known_mask = targets != OOS_LABEL
    oos_classifier = LogisticRegression(C=config.oos_c, **common).fit(
        matrix, targets == OOS_LABEL
    )
    known_classifier = LogisticRegression(C=config.classifier_c, **common).fit(
        matrix[known_mask], targets[known_mask]
    )
    return FrozenEncoderIntentModel(
        config=config,
        oos_classifier=oos_classifier,
        known_classifier=known_classifier,
    )


def classification_metrics(
    expected: Sequence[str],
    predicted: Sequence[str],
    confidences: Sequence[float] | None = None,
) -> dict[str, Any]:
    labels = sorted(set(expected) | set(predicted))
    precision, recall, f1, support = precision_recall_fscore_support(
        expected, predicted, labels=labels, zero_division=0
    )
    per_class = {
        label: {
            "support": int(support[index]),
            "precision": round(float(precision[index]), 6),
            "recall": round(float(recall[index]), 6),
            "f1": round(float(f1[index]), 6),
        }
        for index, label in enumerate(labels)
    }
    result: dict[str, Any] = {
        "total": len(expected),
        "correct": int(sum(left == right for left, right in zip(expected, predicted))),
        "accuracy": round(float(accuracy_score(expected, predicted)), 6),
        "macro_f1": round(float(np.mean(f1)), 6),
        "oos_precision": per_class.get(OOS_LABEL, {}).get("precision", 0.0),
        "oos_recall": per_class.get(OOS_LABEL, {}).get("recall", 0.0),
        "per_class": per_class,
    }
    if confidences is not None:
        correctness = np.asarray(
            [left == right for left, right in zip(expected, predicted)], dtype=np.float64
        )
        scores = np.asarray(confidences, dtype=np.float64)
        result["mean_confidence"] = round(float(scores.mean()), 6)
        result["confidence_brier"] = round(float(np.mean((scores - correctness) ** 2)), 6)
        result["confidence_ece_10"] = round(_ece(scores, correctness), 6)
    return result


def _ece(scores: np.ndarray, correctness: np.ndarray, bins: int = 10) -> float:
    total = max(1, len(scores))
    value = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        mask = (scores >= low) & (scores <= high if index == bins - 1 else scores < high)
        if not mask.any():
            continue
        value += float(mask.sum()) / total * abs(
            float(scores[mask].mean()) - float(correctness[mask].mean())
        )
    return value


def deterministic_train_validation_split(
    labels: Sequence[str], *, validation_fraction: float = 0.2
) -> tuple[np.ndarray, np.ndarray]:
    indices = np.arange(len(labels))
    train, validation = train_test_split(
        indices,
        test_size=validation_fraction,
        random_state=17,
        stratify=np.asarray(labels),
    )
    return np.sort(train), np.sort(validation)


def candidate_configs() -> list[EncoderClassifierConfig]:
    strengths = (0.1, 0.3, 1.0, 3.0, 10.0)
    candidates = [EncoderClassifierConfig("joint", value) for value in strengths]
    for classifier_c in strengths:
        for oos_c in strengths:
            for threshold in np.arange(0.2, 0.81, 0.05):
                candidates.append(
                    EncoderClassifierConfig(
                        "cascade",
                        classifier_c,
                        oos_c,
                        round(float(threshold), 2),
                    )
                )
    return candidates


def select_config(
    train_embeddings: np.ndarray,
    train_labels: Sequence[str],
    validation_embeddings: np.ndarray,
    validation_labels: Sequence[str],
) -> tuple[EncoderClassifierConfig, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for config in candidate_configs():
        model = fit_model(train_embeddings, train_labels, config)
        predicted, confidence = model.predict(validation_embeddings)
        metrics = classification_metrics(validation_labels, predicted, confidence)
        rows.append({"config": config.to_dict(), "metrics": metrics})
    rows.sort(
        key=lambda row: (
            -float(row["metrics"]["macro_f1"]),
            -float(row["metrics"]["accuracy"]),
            -float(row["metrics"]["oos_recall"]),
            0 if row["config"]["architecture"] == "joint" else 1,
            float(row["config"]["classifier_c"]),
            float(row["config"].get("oos_c") or 0.0),
            float(row["config"].get("oos_threshold") or 0.0),
        )
    )
    best = rows[0]["config"]
    return EncoderClassifierConfig(**best), rows
