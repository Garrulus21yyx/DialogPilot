"""Leakage-safe calibration and constrained weight selection for intent fusion."""
from __future__ import annotations

import bisect
import hashlib
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from evaluation.intent_fusion_ablation import (
    FusionConfig,
    V2EvalConfig,
    _classification_metrics,
    evaluate_v2,
    replay_fusion,
)
from evaluation.rag_pipeline.selection import paired_group_bootstrap_delta


SOURCES = ("llm", "ngram", "semantic", "pattern")


@dataclass(frozen=True)
class IsotonicCalibration:
    upper_bounds: tuple[float, ...]
    values: tuple[float, ...]

    def predict(self, score: float) -> float:
        """Map an active source score to empirical correctness; zero remains abstention."""
        value = float(score)
        if value <= 0.0 or not self.upper_bounds:
            return 0.0
        index = min(bisect.bisect_left(self.upper_bounds, value), len(self.values) - 1)
        return self.values[index]

    def to_dict(self) -> dict[str, list[float]]:
        return {
            "upper_bounds": list(self.upper_bounds),
            "values": list(self.values),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IsotonicCalibration":
        return cls(
            tuple(float(value) for value in payload["upper_bounds"]),
            tuple(float(value) for value in payload["values"]),
        )


def fit_isotonic(samples: Sequence[tuple[float, bool]]) -> IsotonicCalibration:
    """Fit a deterministic PAVA reliability map over active top-1 predictions."""
    grouped: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    for score, correct in samples:
        score = float(score)
        if score <= 0.0:
            continue
        grouped[score][0] += int(bool(correct))
        grouped[score][1] += 1
    blocks: list[dict[str, float]] = []
    for score in sorted(grouped):
        successes, count = grouped[score]
        blocks.append({
            "upper": score,
            "successes": float(successes),
            "count": float(count),
        })
        while len(blocks) >= 2:
            left, right = blocks[-2], blocks[-1]
            left_mean = left["successes"] / left["count"]
            right_mean = right["successes"] / right["count"]
            if left_mean <= right_mean:
                break
            blocks[-2:] = [{
                "upper": right["upper"],
                "successes": left["successes"] + right["successes"],
                "count": left["count"] + right["count"],
            }]
    return IsotonicCalibration(
        tuple(block["upper"] for block in blocks),
        tuple(block["successes"] / block["count"] for block in blocks),
    )


def _fit_source_calibrations(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, IsotonicCalibration]:
    models = {}
    for source in SOURCES:
        samples = []
        for case in cases:
            payload = outputs[case["case_id"]][source]
            samples.append((
                float(payload.get("confidence", 0.0) or 0.0),
                str(payload.get("intent") or "other") == case["expected"],
            ))
        models[source] = fit_isotonic(samples)
    return models


def stratified_group_folds(
    cases: Sequence[Mapping[str, Any]],
    *,
    folds: int = 5,
    salt: str = "intent-weight-calibration-v1",
) -> dict[str, int]:
    if folds < 2:
        raise ValueError("fold count must be at least 2")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[str(case["source_group_id"])].append(case)
    by_label: dict[str, list[str]] = defaultdict(list)
    for group_id, rows in grouped.items():
        labels = {str(row["expected"]) for row in rows}
        if len(labels) != 1:
            raise ValueError(f"group {group_id} crosses intent labels")
        by_label[next(iter(labels))].append(group_id)
    group_fold: dict[str, int] = {}
    for label, group_ids in by_label.items():
        ordered = sorted(
            group_ids,
            key=lambda group_id: hashlib.sha256(
                f"{salt}:{label}:{group_id}".encode("utf-8")
            ).hexdigest(),
        )
        for index, group_id in enumerate(ordered):
            group_fold[group_id] = index % folds
    return {
        str(case["case_id"]): group_fold[str(case["source_group_id"])]
        for case in cases
    }


def _refine_by_pattern(
    predicted: str,
    score: float,
    source: Mapping[str, Any],
) -> tuple[str, float]:
    from core.intent_recognizer import IntentCategory, _GENERIC_INTENTS, _SPECIFIC_INTENTS

    pattern = source["pattern"]
    pattern_intent = str(pattern.get("intent") or "other")
    pattern_confidence = float(pattern.get("confidence", 0.0) or 0.0)
    if (
        IntentCategory(predicted) in _GENERIC_INTENTS
        and IntentCategory(pattern_intent) in _SPECIFIC_INTENTS
        and pattern_confidence >= 0.5
        and score < 0.8
    ):
        return pattern_intent, max(score, pattern_confidence)
    return predicted, score


def _predict_calibrated(
    source: Mapping[str, Any],
    calibrated: Mapping[str, float],
    *,
    embedding_source: str,
    llm_weight: float,
    embedding_weight: float,
    pattern_weight: float,
    threshold: float,
    refine_by_pattern: bool,
) -> tuple[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for name, weight in (
        ("llm", llm_weight),
        (embedding_source, embedding_weight),
        ("pattern", pattern_weight),
    ):
        payload = source[name]
        if float(payload.get("confidence", 0.0) or 0.0) <= 0.0 or weight <= 0.0:
            continue
        scores[str(payload.get("intent") or "other")] += weight * calibrated[name]
    if not scores:
        return "other", 0.0
    predicted = min(scores, key=lambda intent: (-scores[intent], intent))
    score = scores[predicted]
    if refine_by_pattern:
        predicted, score = _refine_by_pattern(predicted, score, source)
    return ("other", score) if score < threshold else (predicted, score)


def _candidate_configs() -> list[dict[str, Any]]:
    configs = []
    for embedding_source in ("semantic", "ngram"):
        for llm_units in range(10, 21):  # 0.50 .. 1.00
            for embedding_units in range(0, 9):  # <= 0.40
                pattern_units = 20 - llm_units - embedding_units
                if not 0 <= pattern_units <= 6:  # <= 0.30
                    continue
                for threshold_units in range(6, 17):  # 0.30 .. 0.80
                    for refine in (False, True):
                        llm = llm_units / 20
                        embedding = embedding_units / 20
                        pattern = pattern_units / 20
                        threshold = threshold_units / 20
                        config_id = (
                            f"cal-{embedding_source}-l{llm:.2f}-e{embedding:.2f}"
                            f"-p{pattern:.2f}-t{threshold:.2f}-r{int(refine)}"
                        )
                        configs.append({
                            "config_id": config_id,
                            "embedding_source": embedding_source,
                            "llm_weight": llm,
                            "embedding_weight": embedding,
                            "pattern_weight": pattern,
                            "threshold": threshold,
                            "refine_by_pattern": refine,
                        })
    return configs


def _metrics_for_details(details: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _classification_metrics(
        [str(row["expected"]) for row in details],
        [str(row["predicted"]) for row in details],
        [float(row["confidence"]) for row in details],
    )


def _current_v1_details(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    config = FusionConfig("ngram-current-v1", 0.7, 0.2, 0.1, "ngram")
    details = []
    for case in cases:
        predicted, confidence, _ = replay_fusion(outputs[case["case_id"]], config)
        details.append({
            "case_id": case["case_id"],
            "expected": case["expected"],
            "predicted": predicted,
            "confidence": confidence,
            "correct": predicted == case["expected"],
        })
    return details


def _recall(metrics: Mapping[str, Any], label: str) -> float:
    return float(metrics["per_class"].get(label, {}).get("recall", 0.0) or 0.0)


def select_calibrated_policy(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
    *,
    folds: int = 5,
) -> dict[str, Any]:
    """Select only from dev using out-of-fold calibration and hard safety gates."""
    if not cases or {str(case["analysis_split"]) for case in cases} != {"dev"}:
        raise ValueError("policy selection requires dev cases only")
    missing = [case["case_id"] for case in cases if case["case_id"] not in outputs]
    if missing:
        raise ValueError(f"source outputs missing {len(missing)} dev cases")
    fold_by_id = stratified_group_folds(cases, folds=folds)
    calibrated_by_case: dict[str, dict[str, float]] = {}
    fold_models: dict[str, dict[str, Any]] = {}
    for fold in range(folds):
        train = [case for case in cases if fold_by_id[case["case_id"]] != fold]
        validation = [case for case in cases if fold_by_id[case["case_id"]] == fold]
        models = _fit_source_calibrations(train, outputs)
        fold_models[str(fold)] = {source: model.to_dict() for source, model in models.items()}
        for case in validation:
            source = outputs[case["case_id"]]
            calibrated_by_case[case["case_id"]] = {
                name: models[name].predict(float(source[name].get("confidence", 0.0) or 0.0))
                for name in SOURCES
            }

    baseline_details = _current_v1_details(cases, outputs)
    baseline_metrics = _metrics_for_details(baseline_details)
    baseline_oos = float(baseline_metrics.get("oos_recall") or 0.0)
    baseline_security = _recall(baseline_metrics, "account_security")
    candidates = []
    for config in _candidate_configs():
        details = []
        fold_scores: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for case in cases:
            predicted, confidence = _predict_calibrated(
                outputs[case["case_id"]],
                calibrated_by_case[case["case_id"]],
                **{key: config[key] for key in (
                    "embedding_source", "llm_weight", "embedding_weight",
                    "pattern_weight", "threshold", "refine_by_pattern",
                )},
            )
            detail = {
                "case_id": case["case_id"],
                "expected": case["expected"],
                "predicted": predicted,
                "confidence": confidence,
                "correct": predicted == case["expected"],
            }
            details.append(detail)
            fold_scores[fold_by_id[case["case_id"]]].append(detail)
        metrics = _metrics_for_details(details)
        fold_macro = [_metrics_for_details(fold_scores[index])["macro_f1"] for index in range(folds)]
        eligible = (
            float(metrics.get("oos_recall") or 0.0) >= baseline_oos
            and _recall(metrics, "account_security") >= baseline_security
        )
        candidates.append({
            **config,
            "eligible": eligible,
            "metrics": metrics,
            "fold_macro_f1_mean": statistics.fmean(fold_macro),
            "fold_macro_f1_stdev": statistics.pstdev(fold_macro),
        })

    eligible = [row for row in candidates if row["eligible"]]
    if not eligible:
        raise RuntimeError("no calibrated candidate satisfies V1 OOS/security recall gates")
    selected = max(eligible, key=lambda row: (
        float(row["metrics"]["macro_f1"]),
        float(row["metrics"]["accuracy"]),
        -float(row["fold_macro_f1_stdev"]),
        -float(row["embedding_weight"]),
        -float(row["pattern_weight"]),
        str(row["config_id"]),
    ))
    full_models = _fit_source_calibrations(cases, outputs)
    frozen = {
        "schema_version": 1,
        "selection_split": "dev",
        "config": {key: selected[key] for key in (
            "config_id", "embedding_source", "llm_weight", "embedding_weight",
            "pattern_weight", "threshold", "refine_by_pattern",
        )},
        "calibrations": {source: model.to_dict() for source, model in full_models.items()},
    }
    top = sorted(eligible, key=lambda row: (
        float(row["metrics"]["macro_f1"]), float(row["metrics"]["accuracy"]),
        -float(row["fold_macro_f1_stdev"]), str(row["config_id"]),
    ), reverse=True)[:20]
    return {
        "schema_version": 1,
        "status": "selected_on_dev_not_heldout",
        "case_count": len(cases),
        "fold_count": folds,
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "hard_gates": {
            "oos_recall_ge_current_v1": baseline_oos,
            "account_security_recall_ge_current_v1": baseline_security,
        },
        "current_v1": {"metrics": baseline_metrics, "details": baseline_details},
        "selected_oof": selected,
        "top_eligible": top,
        "frozen_policy": frozen,
        "fold_calibrations": fold_models,
        "limitations": [
            "Source labels are auto-mapped external annotations, not DialogPilot human gold.",
            "Calibration models top-1 correctness, not a full per-class probability distribution.",
            "Only intents with unambiguous external mappings participate in weight selection.",
        ],
    }


def _exact_mcnemar(candidate_only: int, baseline_only: int) -> dict[str, Any]:
    discordant = candidate_only + baseline_only
    if discordant == 0:
        return {"candidate_only": 0, "baseline_only": 0, "exact_p_two_sided": 1.0}
    tail = sum(math.comb(discordant, index) for index in range(min(candidate_only, baseline_only) + 1))
    p_value = min(1.0, 2.0 * tail / (2 ** discordant))
    return {
        "candidate_only": candidate_only,
        "baseline_only": baseline_only,
        "exact_p_two_sided": p_value,
    }


def evaluate_frozen_policy(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
    frozen: Mapping[str, Any],
) -> dict[str, Any]:
    if not cases or len({str(case["analysis_split"]) for case in cases}) != 1:
        raise ValueError("frozen evaluation requires exactly one non-empty split")
    split = str(cases[0]["analysis_split"])
    if split == "dev":
        raise ValueError("use select_calibrated_policy for dev data")
    config = dict(frozen["config"])
    models = {
        source: IsotonicCalibration.from_dict(payload)
        for source, payload in dict(frozen["calibrations"]).items()
    }
    details = []
    for case in cases:
        source = outputs[case["case_id"]]
        calibrated = {
            name: models[name].predict(float(source[name].get("confidence", 0.0) or 0.0))
            for name in SOURCES
        }
        predicted, confidence = _predict_calibrated(
            source,
            calibrated,
            **{key: config[key] for key in (
                "embedding_source", "llm_weight", "embedding_weight",
                "pattern_weight", "threshold", "refine_by_pattern",
            )},
        )
        details.append({
            "case_id": case["case_id"],
            "expected": case["expected"],
            "predicted": predicted,
            "confidence": confidence,
            "correct": predicted == case["expected"],
        })
    baseline_details = _current_v1_details(cases, outputs)
    candidate_metrics = _metrics_for_details(details)
    baseline_metrics = _metrics_for_details(baseline_details)
    candidate_by_id = {row["case_id"]: row for row in details}
    baseline_by_id = {row["case_id"]: row for row in baseline_details}
    candidate_only = sum(
        bool(candidate_by_id[case["case_id"]]["correct"])
        and not bool(baseline_by_id[case["case_id"]]["correct"])
        for case in cases
    )
    baseline_only = sum(
        not bool(candidate_by_id[case["case_id"]]["correct"])
        and bool(baseline_by_id[case["case_id"]]["correct"])
        for case in cases
    )
    bootstrap = paired_group_bootstrap_delta(
        [{"correct": float(row["correct"])} for row in details],
        [{"correct": float(row["correct"])} for row in baseline_details],
        group_ids=[str(case["source_group_id"]) for case in cases],
        metric="correct",
        samples=5000,
        seed=20260831,
    )
    v2 = evaluate_v2(
        cases,
        outputs,
        V2EvalConfig("v2-frozen", semantic_threshold=0.72, semantic_min_margin=0.05),
    )
    return {
        "schema_version": 1,
        "status": "frozen_policy_report_only",
        "split": split,
        "case_count": len(cases),
        "frozen_policy": dict(frozen),
        "current_v1": {"metrics": baseline_metrics, "details": baseline_details},
        "calibrated_candidate": {"metrics": candidate_metrics, "details": details},
        "typed_v2": v2,
        "paired_accuracy_bootstrap": bootstrap,
        "mcnemar": _exact_mcnemar(candidate_only, baseline_only),
    }
