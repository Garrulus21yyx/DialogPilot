"""Train and gate the Target-native fast-path text encoder artifact."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from application.target_encoder_artifact import (
    DEFER_LABEL,
    TARGET_ENCODER_SCHEMA,
    TargetEncoderClass,
    hashed_char_ngram_vector,
)


ONE_SIDED_95_Z = 1.6448536269514722


class TargetEncoderTrainingError(ValueError):
    pass


@dataclass(frozen=True)
class TargetEncoderExample:
    case_id: str
    text: str
    label: str


@dataclass(frozen=True)
class TargetEncoderTrainingConfig:
    artifact_version: str = "target-encoder-zh-v1"
    bundle_version: str = "customer-service-v1"
    target_precision: float = 0.88
    heldout_target_precision: float = 0.98
    heldout_min_accepts: int = 10
    feature_count: int = 8192
    ngram_min: int = 1
    ngram_max: int = 4
    classifier_c: float = 20.0


TARGETS = {
    "general_qa": (
        "general", "tool", "knowledge_search", ("query",),
        ("政策", "规则", "保修", "质保", "发票", "安装", "售后", "配送", "运费", "说明书"),
    ),
    "product_identification": (
        "product_technical", "skill", "product_identification", ("asset_id",),
        ("图", "照片", "附件", "设备", "机器", "实物", "产品"),
    ),
    "refund_status_summary": (
        "billing_refund", "tool", "refund_status", ("order_id",),
        ("退款", "返款", "退回", "退回来", "款项", "原路返回"),
    ),
}


def train_target_encoder(
    *,
    train_path: Path,
    calibration_path: Path,
    heldout_path: Path,
    output_dir: Path,
    config: TargetEncoderTrainingConfig = TargetEncoderTrainingConfig(),
) -> Mapping[str, object]:
    splits = {
        "train": _load(train_path),
        "calibration": _load(calibration_path),
        "heldout": _load(heldout_path),
    }
    expected = {DEFER_LABEL, *TARGETS}
    for split, examples in splits.items():
        labels = {item.label for item in examples}
        if labels != expected:
            raise TargetEncoderTrainingError(f"{split} must cover every frozen label")
    normalized = [
        (split, item.text.strip().lower())
        for split, examples in splits.items()
        for item in examples
    ]
    if len({text for _, text in normalized}) != len(normalized):
        raise TargetEncoderTrainingError("dataset text leaks across splits")

    classifier = LogisticRegression(
        C=config.classifier_c,
        class_weight="balanced",
        max_iter=3000,
        random_state=17,
    ).fit(
        _matrix(splits["train"], config),
        [item.label for item in splits["train"]],
    )
    classes = tuple(str(item) for item in classifier.classes_)
    calibration_probabilities = classifier.predict_proba(
        _matrix(splits["calibration"], config)
    )
    calibrations = {
        label: _select_threshold(
            label,
            classes,
            calibration_probabilities,
            tuple(item.label for item in splits["calibration"]),
            config.target_precision,
        )
        for label in TARGETS
    }
    heldout_probabilities = classifier.predict_proba(
        _matrix(splits["heldout"], config)
    )
    heldout_expected = tuple(item.label for item in splits["heldout"])
    class_records = []
    for label, (
        owner, capability_kind, capability_id, required_arguments,
        required_signal_terms,
    ) in TARGETS.items():
        calibration = calibrations[label]
        heldout = _evaluate_threshold(
            label, classes, heldout_probabilities, heldout_expected,
            calibration["threshold"],
        )
        enabled = bool(
            calibration["enabled"]
            and heldout["accepted"] >= config.heldout_min_accepts
            and heldout["correct"] / heldout["accepted"]
            >= config.heldout_target_precision
        )
        class_records.append(TargetEncoderClass(
            label=label,
            owner_agent=owner,
            capability_kind=capability_kind,
            capability_id=capability_id,
            required_arguments=required_arguments,
            required_signal_terms=required_signal_terms,
            threshold=float(calibration["threshold"]),
            enabled=enabled,
            calibration_accepted=int(calibration["accepted"]),
            calibration_correct=int(calibration["correct"]),
            calibration_precision_lower_bound=float(
                calibration["precision_lower_bound"]
            ),
            heldout_accepted=int(heldout["accepted"]),
            heldout_correct=int(heldout["correct"]),
            heldout_precision_lower_bound=float(heldout["precision_lower_bound"]),
        ))
    if not any(item.enabled for item in class_records):
        raise TargetEncoderTrainingError("no target class passed heldout gating")

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.json"
    manifest_path = output_dir / "manifest.json"
    if model_path.exists() or manifest_path.exists():
        raise TargetEncoderTrainingError("output artifact already exists")
    model = {
        "model_type": "hashed-char-ngram-logistic-regression-v1",
        "feature_count": config.feature_count,
        "ngram_min": config.ngram_min,
        "ngram_max": config.ngram_max,
        "classes": list(classes),
        "coefficients": classifier.coef_.tolist(),
        "intercepts": classifier.intercept_.tolist(),
    }
    model_path.write_text(
        json.dumps(model, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    total_heldout = len(splits["heldout"])
    accepted_total = sum(item.heldout_accepted for item in class_records if item.enabled)
    correct_total = sum(item.heldout_correct for item in class_records if item.enabled)
    manifest = {
        "schema_version": TARGET_ENCODER_SCHEMA,
        "status": "ACTIVE",
        "artifact_version": config.artifact_version,
        "bundle_version": config.bundle_version,
        "model_filename": model_path.name,
        "model_sha256": _file_sha256(model_path),
        "target_precision": config.target_precision,
        "heldout_target_precision": config.heldout_target_precision,
        "heldout_min_accepts": config.heldout_min_accepts,
        "classes": [asdict(item) for item in class_records],
        "datasets": [
            {"split": split, "sha256": _file_sha256(path)}
            for split, path in (
                ("train", train_path),
                ("calibration", calibration_path),
                ("heldout", heldout_path),
            )
        ],
        "heldout_summary": {
            "total": total_heldout,
            "accepted": accepted_total,
            "correct": correct_total,
            "accepted_precision": correct_total / accepted_total if accepted_total else 0.0,
            "coverage": accepted_total / total_heldout,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _load(path: Path) -> tuple[TargetEncoderExample, ...]:
    values = []
    seen = set()
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError as exc:
        raise TargetEncoderTrainingError(f"dataset unavailable: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            item = TargetEncoderExample(
                str(value["case_id"]), str(value["text"]), str(value["label"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TargetEncoderTrainingError(
                f"invalid dataset row {path}:{line_number}"
            ) from exc
        if not item.case_id.strip() or not item.text.strip() or item.case_id in seen:
            raise TargetEncoderTrainingError("dataset case identity is invalid")
        seen.add(item.case_id)
        values.append(item)
    if not values:
        raise TargetEncoderTrainingError("dataset split is empty")
    return tuple(values)


def _matrix(
    examples: Sequence[TargetEncoderExample],
    config: TargetEncoderTrainingConfig,
) -> np.ndarray:
    matrix = np.zeros((len(examples), config.feature_count), dtype=np.float32)
    for row, example in enumerate(examples):
        for index, value in hashed_char_ngram_vector(
            example.text,
            feature_count=config.feature_count,
            ngram_min=config.ngram_min,
            ngram_max=config.ngram_max,
        ).items():
            matrix[row, index] = value
    return matrix


def _select_threshold(label, classes, probabilities, expected, target_precision):
    index = classes.index(label)
    winners = probabilities.argmax(axis=1)
    thresholds = sorted({
        float(probabilities[row, index])
        for row, winner in enumerate(winners)
        if int(winner) == index
    })
    best = None
    for threshold in thresholds:
        result = _evaluate_threshold(label, classes, probabilities, expected, threshold)
        if result["precision_lower_bound"] >= target_precision and (
            best is None or result["accepted"] > best["accepted"]
        ):
            best = result
    return best or {
        "threshold": 1.0,
        "enabled": False,
        "accepted": 0,
        "correct": 0,
        "precision_lower_bound": 0.0,
    }


def _evaluate_threshold(label, classes, probabilities, expected, threshold):
    index = classes.index(label)
    winners = probabilities.argmax(axis=1)
    accepted_rows = [
        row for row, winner in enumerate(winners)
        if int(winner) == index and float(probabilities[row, index]) >= threshold
    ]
    correct = sum(expected[row] == label for row in accepted_rows)
    return {
        "threshold": threshold,
        "enabled": bool(accepted_rows),
        "accepted": len(accepted_rows),
        "correct": correct,
        "precision_lower_bound": _wilson_lower_bound(correct, len(accepted_rows)),
    }


def _wilson_lower_bound(correct: int, total: int) -> float:
    if total == 0:
        return 0.0
    proportion = correct / total
    z_squared = ONE_SIDED_95_Z**2
    denominator = 1.0 + z_squared / total
    centre = proportion + z_squared / (2.0 * total)
    radius = ONE_SIDED_95_Z * math.sqrt(
        (proportion * (1.0 - proportion) + z_squared / (4.0 * total)) / total
    )
    return (centre - radius) / denominator


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
