"""Bounded, reproducible diagnostics for DialogPilot intent-signal fusion."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.intent_recognizer import (
    IntentCategory,
    _GENERIC_INTENTS,
    _SPECIFIC_INTENTS,
)
from core.intent_fusion_v2 import (
    EvidencePolarity,
    FusionPolicyV2,
    PatternEvidence,
    SemanticCandidate,
    SemanticEvidence,
)
from evaluation.dataset import DatasetBundle


@dataclass(frozen=True)
class FusionConfig:
    name: str
    llm_weight: float
    embedding_weight: float
    pattern_weight: float
    embedding_source: str | None
    threshold: float = 0.5
    refine_by_pattern: bool = True


@dataclass(frozen=True)
class V2EvalConfig:
    name: str
    semantic_threshold: float
    semantic_min_margin: float
    llm_accept_threshold: float = 0.5
    out_of_scope_threshold: float = 0.7


def _stable(rows: Iterable[Mapping[str, Any]], salt: str) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: hashlib.sha256(
            f"{salt}:{row['id']}".encode("utf-8")
        ).hexdigest(),
    )


def _stratified(
    rows: Sequence[Mapping[str, Any]],
    *,
    label_key,
    quotas: Mapping[str, int],
    salt: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(label_key(row))].append(row)
    selected: list[dict[str, Any]] = []
    for label, count in quotas.items():
        candidates = _stable(grouped[label], f"{salt}:{label}")
        if len(candidates) < count:
            raise ValueError(f"slice {salt} requires {count} {label} rows, got {len(candidates)}")
        selected.extend(candidates[:count])
    return selected


def _case(
    source: Mapping[str, Any],
    *,
    slice_name: str,
    expected: str,
) -> dict[str, Any]:
    source_group = str(source.get("group_id") or source["id"])
    split_digest = hashlib.sha256(
        f"intent-fusion-mini-v1:{source_group}".encode("utf-8")
    ).digest()
    return {
        "case_id": f"{slice_name}:{source['id']}",
        "slice": slice_name,
        "analysis_split": "dev" if split_digest[0] < 179 else "regression",
        "message": str(source["input"]["message"]),
        "expected": expected,
        "source_case_id": str(source["id"]),
        "source_group_id": source_group,
        "source_split": str(source.get("split") or "unknown"),
        "source_review": dict(source.get("review") or {}),
        "source": dict(source.get("source") or {}),
        "tags": list(source.get("tags") or []),
    }


def build_cases(dataset_path: Path) -> list[dict[str, Any]]:
    """Build four diagnostic slices entirely from versioned repository data."""
    rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line]
    intent = [row for row in rows if row.get("layer") == "intent"]
    routing = [row for row in rows if row.get("layer") == "routing"]

    business_quotas = {
        "account": 7,
        "account_security": 8,
        "logistics": 7,
        "payment_issue": 7,
        "refund": 7,
        "technical": 7,
        "technical_login": 7,
    }
    business_rows = _stratified(
        [row for row in intent if row["expected"]["intent"] != "other"],
        label_key=lambda row: row["expected"]["intent"],
        quotas=business_quotas,
        salt="business",
    )
    used_intent = {row["id"] for row in business_rows}

    oos_rows = _stable(
        [row for row in intent if row["expected"]["intent"] == "other"],
        "rejection:oos",
    )[:30]
    in_scope_rows = _stable(
        [row for row in intent if row["id"] not in used_intent and row["expected"]["intent"] != "other"],
        "rejection:in-scope",
    )[:20]

    conflict_priority = [
        row for row in routing
        if "multi-agent" in row.get("tags", [])
        or str(row.get("group_id", "")).startswith("routing-negated_")
    ]
    conflict_extra = [
        row for row in routing
        if row not in conflict_priority
        and row["input"].get("intent") in {
            "account_security", "billing", "payment_issue", "technical", "technical_login"
        }
    ]
    conflict_rows = _stable(conflict_priority, "conflict:priority")
    conflict_rows += _stable(conflict_extra, "conflict:extra")[: 50 - len(conflict_rows)]
    conflict_rows = conflict_rows[:50]

    conflict_ids = {row["id"] for row in conflict_rows}
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in routing:
        if row["id"] in conflict_ids or "multi-agent" in row.get("tags", []):
            continue
        if str(row.get("group_id", "")).startswith("routing-negated_"):
            continue
        grouped[str(row["group_id"])].append(row)
    candidate_groups = [
        group_rows for group_rows in grouped.values()
        if len(group_rows) == 4
    ]
    candidate_groups.sort(
        key=lambda group_rows: hashlib.sha256(
            f"similarity:{group_rows[0]['group_id']}".encode("utf-8")
        ).hexdigest()
    )
    similarity_rows = list(itertools.chain.from_iterable(candidate_groups[:13]))

    cases: list[dict[str, Any]] = []
    cases.extend(
        _case(row, slice_name="business_boundary", expected=row["expected"]["intent"])
        for row in business_rows
    )
    cases.extend(
        _case(row, slice_name="rejection", expected=row["expected"]["intent"])
        for row in oos_rows + in_scope_rows
    )
    cases.extend(
        _case(row, slice_name="conflict", expected=row["input"]["intent"])
        for row in conflict_rows
    )
    cases.extend(
        _case(row, slice_name="semantic_similarity", expected=row["input"]["intent"])
        for row in similarity_rows
    )
    return cases


def load_reviewed_candidate_cases(candidate_path: Path) -> list[dict[str, Any]]:
    """Load the fixed 100-case independently reviewed synthetic contract.

    Labels and review metadata are retained for offline scoring only. Review notes
    are deliberately not copied into the inference case representation.
    """
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(candidate_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on candidate line {line_number}: {exc}") from exc
    if len(rows) != 100:
        raise ValueError(f"reviewed candidate suite requires exactly 100 rows, got {len(rows)}")

    expected_slices = {
        "business_boundary": 25,
        "rejection": 25,
        "conflict": 25,
        "semantic_similarity": 25,
    }
    slice_counts = Counter(str(row.get("slice")) for row in rows)
    if slice_counts != Counter(expected_slices):
        raise ValueError(f"unexpected candidate slice counts: {dict(slice_counts)}")

    allowed_intents = {intent.value for intent in IntentCategory}
    seen_ids: set[str] = set()
    seen_messages: set[str] = set()
    cases: list[dict[str, Any]] = []
    for row in rows:
        case_id = str(row.get("id") or "")
        if not case_id or case_id in seen_ids:
            raise ValueError(f"missing or duplicate candidate id: {case_id!r}")
        seen_ids.add(case_id)
        if row.get("schema_version") != 1 or row.get("layer") != "intent":
            raise ValueError(f"unsupported candidate contract for {case_id}")
        review = row.get("review") or {}
        if review.get("status") != "independently_reviewed_synthetic":
            raise ValueError(f"candidate {case_id} is not independently reviewed")

        input_payload = row.get("input") or {}
        message = input_payload.get("message")
        history = input_payload.get("history")
        if not isinstance(message, str) or not message.strip() or message in seen_messages:
            raise ValueError(f"candidate {case_id} has missing or duplicate message")
        seen_messages.add(message)
        if not isinstance(history, list) or len(history) > 3:
            raise ValueError(f"candidate {case_id} history must be a list of at most 3 messages")
        normalized_history: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                raise ValueError(f"candidate {case_id} has invalid history role")
            if not isinstance(item.get("content"), str) or not item["content"].strip():
                raise ValueError(f"candidate {case_id} has invalid history content")
            normalized_history.append({"role": item["role"], "content": item["content"]})

        expected_payload = row.get("expected") or {}
        expected = expected_payload.get("intent")
        secondary = expected_payload.get("secondary_intents")
        if expected not in allowed_intents:
            raise ValueError(f"candidate {case_id} has unsupported intent: {expected!r}")
        if not isinstance(secondary, list) or any(item not in allowed_intents for item in secondary):
            raise ValueError(f"candidate {case_id} has unsupported secondary intents")
        if expected in secondary or (expected == "other" and secondary):
            raise ValueError(f"candidate {case_id} has contradictory secondary intents")

        cases.append({
            "case_id": case_id,
            "slice": str(row["slice"]),
            "analysis_split": "sealed_validation",
            "message": message,
            "history": normalized_history,
            "expected": expected,
            "secondary_intents": list(secondary),
            "source_case_id": case_id,
            "source_group_id": str(row.get("group_id") or case_id),
            "source_review_status": str(review["status"]),
            "tags": list(row.get("tags") or []),
        })
    return cases


def load_external_intent_cases(bundle_path: Path, *, split: str) -> list[dict[str, Any]]:
    """Load an upstream-owned intent split without copying review notes to inference cases."""
    if split not in {"dev", "heldout"}:
        raise ValueError("external intent split must be dev or heldout")
    bundle = DatasetBundle.load(bundle_path)
    selected = bundle.select(layer="intent", split=split)
    if not selected:
        raise ValueError(f"external intent bundle has no {split} cases")
    cases = []
    for case in selected:
        expected = str(case.expected.get("intent") or "")
        if expected not in {intent.value for intent in IntentCategory}:
            raise ValueError(f"{case.case_id} has unsupported intent {expected!r}")
        cases.append({
            "case_id": case.case_id,
            "slice": "external_calibration",
            "analysis_split": split,
            "message": str(case.input["message"]),
            "history": [],
            "expected": expected,
            "source_case_id": case.case_id,
            "source_group_id": case.group_id,
            "source_dataset": str(case.source.get("dataset") or ""),
            "source_original_label": str(case.source.get("original_label") or ""),
            "source_review_status": str(case.review.get("status") or ""),
            "tags": list(case.tags),
        })
    return cases


def replay_fusion(
    sources: Mapping[str, Mapping[str, Any]],
    config: FusionConfig,
) -> tuple[str, float, dict[str, float]]:
    """Replay the production fusion algebra from cached source outputs."""
    llm = sources["llm"]
    pattern = sources["pattern"]
    embedding = (
        sources[config.embedding_source]
        if config.embedding_source is not None
        else {"intent": "other", "confidence": 0.0}
    )
    source_scores = {
        "llm": float(llm.get("confidence", 0.0) or 0.0),
        "embedding": float(embedding.get("confidence", 0.0) or 0.0),
        "pattern": float(pattern.get("confidence", 0.0) or 0.0),
    }
    if llm.get("failed"):
        if embedding.get("intent") != "other" and source_scores["embedding"] > 0:
            return str(embedding["intent"]), source_scores["embedding"], source_scores
        if pattern.get("intent") != "other" and source_scores["pattern"] > 0:
            return str(pattern["intent"]), source_scores["pattern"], source_scores
        return "other", 0.0, source_scores

    weighted = [
        (llm, config.llm_weight),
        (embedding, config.embedding_weight),
        (pattern, config.pattern_weight),
    ]
    scores: dict[str, float] = defaultdict(float)
    for result, weight in weighted:
        scores[str(result.get("intent") or "other")] += weight * float(
            result.get("confidence", 0.0) or 0.0
        )
    best = max(scores, key=scores.get)
    best_score = scores[best]
    pattern_intent = str(pattern.get("intent") or "other")
    pattern_confidence = source_scores["pattern"]
    if (
        config.refine_by_pattern
        and IntentCategory(best) in _GENERIC_INTENTS
        and IntentCategory(pattern_intent) in _SPECIFIC_INTENTS
        and pattern_confidence >= 0.5
        and best_score < 0.8
    ):
        source_scores["refined_by_pattern"] = pattern_confidence
        return pattern_intent, max(best_score, pattern_confidence), source_scores
    if best_score < config.threshold:
        return "other", best_score, source_scores
    return best, best_score, source_scores


def _classification_metrics(
    expected: Sequence[str],
    predicted: Sequence[str],
    confidences: Sequence[float] | None = None,
) -> dict[str, Any]:
    labels = sorted(set(expected) | set(predicted))
    per_class: dict[str, dict[str, float | int]] = {}
    for label in labels:
        tp = sum(gold == label and actual == label for gold, actual in zip(expected, predicted))
        fp = sum(gold != label and actual == label for gold, actual in zip(expected, predicted))
        fn = sum(gold == label and actual != label for gold, actual in zip(expected, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "support": sum(gold == label for gold in expected),
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
    accuracy = sum(gold == actual for gold, actual in zip(expected, predicted)) / len(expected)
    macro_f1 = sum(float(row["f1"]) for row in per_class.values()) / len(per_class)
    other_gold = [gold == "other" for gold in expected]
    other_pred = [actual == "other" for actual in predicted]
    oos_tp = sum(gold and actual for gold, actual in zip(other_gold, other_pred))
    oos_fp = sum(not gold and actual for gold, actual in zip(other_gold, other_pred))
    oos_fn = sum(gold and not actual for gold, actual in zip(other_gold, other_pred))
    metrics: dict[str, Any] = {
        "total": len(expected),
        "correct": sum(gold == actual for gold, actual in zip(expected, predicted)),
        "accuracy": round(accuracy, 6),
        "macro_f1": round(macro_f1, 6),
        "oos_precision": round(oos_tp / (oos_tp + oos_fp), 6) if oos_tp + oos_fp else None,
        "oos_recall": round(oos_tp / (oos_tp + oos_fn), 6) if oos_tp + oos_fn else None,
        "per_class": per_class,
    }
    if confidences is not None:
        correctness = [float(gold == actual) for gold, actual in zip(expected, predicted)]
        ece = 0.0
        for lower in (index / 10 for index in range(10)):
            upper = lower + 0.1
            indexes = [
                index for index, confidence in enumerate(confidences)
                if lower <= confidence < upper or (upper >= 1.0 and confidence == 1.0)
            ]
            if not indexes:
                continue
            bin_accuracy = sum(correctness[index] for index in indexes) / len(indexes)
            bin_confidence = sum(confidences[index] for index in indexes) / len(indexes)
            ece += len(indexes) / len(confidences) * abs(bin_accuracy - bin_confidence)
        metrics.update({
            "mean_confidence": round(sum(confidences) / len(confidences), 6),
            "confidence_ece_10": round(ece, 6),
            "confidence_brier": round(sum(
                (confidence - correct) ** 2
                for confidence, correct in zip(confidences, correctness)
            ) / len(confidences), 6),
        })
    return metrics


def evaluate_config(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
    config: FusionConfig,
) -> dict[str, Any]:
    details = []
    for case in cases:
        predicted, confidence, evidence = replay_fusion(outputs[case["case_id"]], config)
        details.append({
            "case_id": case["case_id"],
            "slice": case["slice"],
            "analysis_split": case["analysis_split"],
            "expected": case["expected"],
            "predicted": predicted,
            "confidence": round(confidence, 6),
            "correct": predicted == case["expected"],
            "evidence": evidence,
        })
    by_scope: dict[str, Any] = {}
    scopes = {"all": details}
    for split in sorted({str(row["analysis_split"]) for row in details}):
        scopes[split] = [row for row in details if row["analysis_split"] == split]
    for slice_name in sorted({str(case["slice"]) for case in cases}):
        scopes[f"slice:{slice_name}"] = [row for row in details if row["slice"] == slice_name]
    for name, rows in scopes.items():
        by_scope[name] = _classification_metrics(
            [row["expected"] for row in rows],
            [row["predicted"] for row in rows],
            [row["confidence"] for row in rows],
        )
    return {"config": config.__dict__, "metrics": by_scope, "details": details}


def _semantic_evidence(
    payload: Mapping[str, Any],
    config: V2EvalConfig,
) -> SemanticEvidence:
    if payload.get("failed"):
        return SemanticEvidence.failure("bge-m3", str(payload.get("error_type") or "Failure"))
    top1_score = float(payload.get("confidence", 0.0) or 0.0)
    top2_score = float(payload.get("second_confidence", 0.0) or 0.0)
    margin = float(payload.get("margin", top1_score - top2_score) or 0.0)
    return SemanticEvidence(
        provider="bge-m3",
        top1=SemanticCandidate(IntentCategory(str(payload.get("intent") or "other")), top1_score),
        top2=SemanticCandidate(IntentCategory(str(payload.get("second_intent") or "other")), top2_score),
        margin=margin,
        threshold=config.semantic_threshold,
        min_margin=config.semantic_min_margin,
        accepted=top1_score >= config.semantic_threshold and margin >= config.semantic_min_margin,
    )


def _pattern_evidence(payload: Sequence[Mapping[str, Any]]) -> tuple[PatternEvidence, ...]:
    return tuple(
        PatternEvidence(
            intent=IntentCategory(str(item["intent"])),
            keyword=str(item["keyword"]),
            span=str(item["span"]),
            start=int(item["start"]),
            end=int(item["end"]),
            polarity=EvidencePolarity(str(item["polarity"])),
            specific=bool(item["specific"]),
        )
        for item in payload
    )


def evaluate_v2(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
    config: V2EvalConfig,
) -> dict[str, Any]:
    policy = FusionPolicyV2(
        llm_accept_threshold=config.llm_accept_threshold,
        out_of_scope_threshold=config.out_of_scope_threshold,
    )
    details = []
    for case in cases:
        source = outputs[case["case_id"]]
        decision = policy.decide(
            source["llm"],
            _semantic_evidence(source["semantic"], config),
            _pattern_evidence(source.get("pattern_evidence") or []),
        )
        details.append({
            "case_id": case["case_id"],
            "slice": case["slice"],
            "analysis_split": case["analysis_split"],
            "expected": case["expected"],
            "predicted": decision.intent.value,
            "confidence": round(decision.support_score, 6),
            "confidence_kind": decision.confidence_kind,
            "status": decision.status.value,
            "reason": decision.reason,
            "correct": decision.intent.value == case["expected"],
            "source_intents": dict(decision.source_intents),
        })
    scopes: dict[str, list[dict[str, Any]]] = {"all": details}
    for split in sorted({str(row["analysis_split"]) for row in details}):
        scopes[split] = [row for row in details if row["analysis_split"] == split]
    for slice_name in sorted({str(case["slice"]) for case in cases}):
        scopes[f"slice:{slice_name}"] = [row for row in details if row["slice"] == slice_name]
    metrics = {
        name: _classification_metrics(
            [row["expected"] for row in rows],
            [row["predicted"] for row in rows],
            [row["confidence"] for row in rows],
        )
        for name, rows in scopes.items()
    }
    return {
        "config": config.__dict__,
        "metrics": metrics,
        "status_counts": dict(Counter(row["status"] for row in details)),
        "reason_counts": dict(Counter(row["reason"] for row in details)),
        "details": details,
    }


def coarse_configs(*, embedding_source: str) -> list[FusionConfig]:
    configs: list[FusionConfig] = []
    for llm_weight in (0.5, 0.6, 0.7, 0.8, 0.9):
        for embedding_weight in (0.0, 0.1, 0.2, 0.3):
            pattern_weight = round(1.0 - llm_weight - embedding_weight, 10)
            if not 0.0 <= pattern_weight <= 0.3:
                continue
            for threshold in (0.4, 0.5, 0.6, 0.7):
                for refine in (False, True):
                    configs.append(FusionConfig(
                        name=(
                            f"grid-{embedding_source}-l{llm_weight:.1f}"
                            f"-e{embedding_weight:.1f}-p{pattern_weight:.1f}"
                            f"-t{threshold:.1f}-r{int(refine)}"
                        ),
                        llm_weight=llm_weight,
                        embedding_weight=embedding_weight,
                        pattern_weight=pattern_weight,
                        embedding_source=embedding_source if embedding_weight else None,
                        threshold=threshold,
                        refine_by_pattern=refine,
                    ))
    return configs


def _dev_rank(row: Mapping[str, Any]) -> tuple[float, ...]:
    dev = row["metrics"]["dev"]
    security = dev["per_class"].get("account_security", {}).get("recall", 0.0)
    oos = dev.get("oos_recall")
    config = row["config"]
    return (
        dev["macro_f1"],
        float(security),
        float(oos if oos is not None else 0.0),
        -float(config["embedding_weight"]),
        -float(config["pattern_weight"]),
    )


def choose_dev_config(results: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Choose by dev Macro-F1, then security recall, OOS recall, and lower complexity."""
    return max(results, key=_dev_rank)


def _source_metrics(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
    source: str,
) -> dict[str, Any]:
    rows = [
        {
            "slice": case["slice"],
            "analysis_split": case["analysis_split"],
            "expected": case["expected"],
            "predicted": outputs[case["case_id"]][source]["intent"],
            "confidence": float(outputs[case["case_id"]][source]["confidence"]),
        }
        for case in cases
    ]
    scopes = {"all": rows}
    for split in sorted({str(row["analysis_split"]) for row in rows}):
        scopes[split] = [row for row in rows if row["analysis_split"] == split]
    for slice_name in sorted({str(case["slice"]) for case in cases}):
        scopes[f"slice:{slice_name}"] = [row for row in rows if row["slice"] == slice_name]
    return {
        name: _classification_metrics(
            [row["expected"] for row in scope],
            [row["predicted"] for row in scope],
            [row["confidence"] for row in scope],
        )
        for name, scope in scopes.items()
    }


def summary_report(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
    *,
    semantic_model: str,
) -> dict[str, Any]:
    fixed = [
        FusionConfig("llm-only", 1.0, 0.0, 0.0, None, refine_by_pattern=False),
        FusionConfig("llm-pattern-current", 0.85, 0.0, 0.15, None),
        FusionConfig("ngram-current", 0.7, 0.2, 0.1, "ngram"),
        FusionConfig("ngram-no-refine", 0.7, 0.2, 0.1, "ngram", refine_by_pattern=False),
        FusionConfig("semantic-current", 0.7, 0.2, 0.1, "semantic"),
        FusionConfig("semantic-no-refine", 0.7, 0.2, 0.1, "semantic", refine_by_pattern=False),
    ]
    fixed_results = [evaluate_config(cases, outputs, config) for config in fixed]
    grid_configs = [
        config
        for source in ("ngram", "semantic")
        for config in coarse_configs(embedding_source=source)
    ]
    unique_configs: dict[tuple[Any, ...], FusionConfig] = {}
    for config in grid_configs:
        key = (
            config.llm_weight,
            config.embedding_weight,
            config.pattern_weight,
            config.embedding_source,
            config.threshold,
            config.refine_by_pattern,
        )
        unique_configs.setdefault(key, config)
    grid_results = [
        evaluate_config(cases, outputs, config)
        for config in unique_configs.values()
    ]
    selected = choose_dev_config(grid_results)
    top_dev = sorted(grid_results, key=_dev_rank, reverse=True)[:10]
    v2_default = evaluate_v2(
        cases,
        outputs,
        V2EvalConfig("v2-default", semantic_threshold=0.72, semantic_min_margin=0.05),
    )
    v2_candidates = [
        evaluate_v2(
            cases,
            outputs,
            V2EvalConfig(
                f"v2-s{threshold:.2f}-m{margin:.2f}",
                semantic_threshold=threshold,
                semantic_min_margin=margin,
            ),
        )
        for threshold in (0.65, 0.70, 0.72, 0.75, 0.80)
        for margin in (0.00, 0.03, 0.05, 0.08, 0.10)
    ]

    def v2_rank(row: Mapping[str, Any]) -> tuple[float, ...]:
        dev = row["metrics"]["dev"]
        security = dev["per_class"].get("account_security", {}).get("recall", 0.0)
        oos = dev.get("oos_recall")
        config = row["config"]
        return (
            dev["macro_f1"],
            float(security),
            float(oos if oos is not None else 0.0),
            dev["accuracy"],
            float(config["semantic_threshold"]),
            float(config["semantic_min_margin"]),
        )

    v2_selected = max(v2_candidates, key=v2_rank)
    v2_top_dev = sorted(v2_candidates, key=v2_rank, reverse=True)[:10]
    llm_failures = sum(bool(outputs[case["case_id"]]["llm"].get("failed")) for case in cases)
    disagreements = Counter()
    for case in cases:
        source = outputs[case["case_id"]]
        disagreements["llm_pattern"] += source["llm"]["intent"] != source["pattern"]["intent"]
        disagreements["ngram_semantic"] += source["ngram"]["intent"] != source["semantic"]["intent"]
    return {
        "schema_version": 1,
        "status": "diagnostic_regression_not_fresh_gold",
        "case_count": len(cases),
        "slice_counts": dict(Counter(case["slice"] for case in cases)),
        "analysis_split_counts": dict(Counter(case["analysis_split"] for case in cases)),
        "semantic_model": semantic_model,
        "llm_failures": llm_failures,
        "source_disagreements": dict(disagreements),
        "source_metrics": {
            source: _source_metrics(cases, outputs, source)
            for source in ("llm", "ngram", "semantic", "pattern")
        },
        "fixed_results": fixed_results,
        "selected_on_dev": selected,
        "top_dev_candidates": [
            {"config": row["config"], "dev": row["metrics"]["dev"]}
            for row in top_dev
        ],
        "v2_default": v2_default,
        "v2_selected_on_dev": v2_selected,
        "v2_top_dev_candidates": [
            {"config": row["config"], "dev": row["metrics"]["dev"]}
            for row in v2_top_dev
        ],
        "v2_candidate_count": len(v2_candidates),
        "grid_candidate_count": len(grid_results),
        "limitations": [
            "BANKING77 mappings are auto-mapped and project routing labels are provisional.",
            "The analysis regression split is consumed, not a fresh heldout.",
            "Conflict cases score only the declared primary intent, not multi-label coverage.",
            "LLM confidence is self-reported and is not a calibrated probability.",
        ],
    }


def sealed_validation_report(
    cases: Sequence[Mapping[str, Any]],
    outputs: Mapping[str, Mapping[str, Any]],
    *,
    semantic_model: str,
    candidate_sha256: str,
) -> dict[str, Any]:
    """Score frozen V1/V2 policies without searching weights or thresholds."""
    fixed_configs = [
        FusionConfig("llm-only", 1.0, 0.0, 0.0, None, refine_by_pattern=False),
        FusionConfig("ngram-current-v1", 0.7, 0.2, 0.1, "ngram"),
        FusionConfig("semantic-v1-algebra", 0.7, 0.2, 0.1, "semantic"),
    ]
    fixed_results = [evaluate_config(cases, outputs, config) for config in fixed_configs]
    v1 = next(row for row in fixed_results if row["config"]["name"] == "ngram-current-v1")
    v2 = evaluate_v2(
        cases,
        outputs,
        V2EvalConfig("v2-frozen", semantic_threshold=0.72, semantic_min_margin=0.05),
    )
    v1_by_id = {row["case_id"]: row for row in v1["details"]}
    v2_by_id = {row["case_id"]: row for row in v2["details"]}
    fixes = [case_id for case_id in v1_by_id if not v1_by_id[case_id]["correct"] and v2_by_id[case_id]["correct"]]
    harms = [case_id for case_id in v1_by_id if v1_by_id[case_id]["correct"] and not v2_by_id[case_id]["correct"]]
    llm_failures = sum(bool(outputs[case["case_id"]]["llm"].get("failed")) for case in cases)
    disagreements = Counter()
    for case in cases:
        source = outputs[case["case_id"]]
        disagreements["llm_pattern"] += source["llm"]["intent"] != source["pattern"]["intent"]
        disagreements["ngram_semantic"] += source["ngram"]["intent"] != source["semantic"]["intent"]
    return {
        "schema_version": 1,
        "status": "independently_reviewed_synthetic_sealed_validation",
        "candidate_sha256": candidate_sha256,
        "case_count": len(cases),
        "slice_counts": dict(Counter(case["slice"] for case in cases)),
        "semantic_model": semantic_model,
        "policy_selection": "frozen before scoring; no grid search or threshold tuning",
        "llm_failures": llm_failures,
        "source_disagreements": dict(disagreements),
        "source_metrics": {
            source: _source_metrics(cases, outputs, source)
            for source in ("llm", "ngram", "semantic", "pattern")
        },
        "fixed_results": fixed_results,
        "v1_current": v1,
        "v2_frozen": v2,
        "v2_vs_v1": {"fixes": fixes, "harms": harms},
        "limitations": [
            "The labels were generated and independently reviewed by LLMs; they are not human gold.",
            "Primary-intent accuracy does not score secondary_intents as multi-label outputs.",
            "LLM confidence is self-reported and is not a calibrated probability.",
            "This sealed suite must not be reused to tune the reported frozen policies.",
        ],
    }
