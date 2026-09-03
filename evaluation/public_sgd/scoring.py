"""Deterministic exact-match scorer for adapted SGD command predictions."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from evaluation.public_sgd.adapter import CASE_SCHEMA_VERSION
from evaluation.public_sgd.contracts import SgdAdapterError, load_jsonl


def score_predictions(
    cases_path: Path,
    predictions_path: Path,
) -> Mapping[str, Any]:
    cases = load_jsonl(cases_path)
    predictions = load_jsonl(predictions_path)
    by_id: dict[str, Mapping[str, Any]] = {}
    for prediction in predictions:
        case_id = str(prediction.get("case_id") or "")
        if not case_id or case_id in by_id:
            raise SgdAdapterError("predictions require unique non-empty case_id")
        by_id[case_id] = prediction

    totals: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    status_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    rule_totals: Counter[str] = Counter()
    rule_correct: Counter[str] = Counter()
    failures: list[Mapping[str, Any]] = []
    known_ids: set[str] = set()
    for case in cases:
        if case.get("schema_version") != CASE_SCHEMA_VERSION:
            raise SgdAdapterError("case schema version is unsupported")
        case_id = str(case["case_id"])
        if case_id in known_ids:
            raise SgdAdapterError("cases require unique case_id")
        known_ids.add(case_id)
        expected = case["expected"]
        actual = by_id.get(case_id)
        expected_status = str(expected["status"])
        mapping_rule = str(case["provenance"]["mapping_rule"])
        actual_status = str((actual or {}).get("status") or "MISSING")
        status_confusion[expected_status][actual_status] += 1
        totals.update(("status", "next_state", "exact"))
        if expected_status == "RESOLVED":
            totals.update(("command", "flow", "arguments"))

        checks = {
            "status": actual_status == expected_status,
            "command": _command_kinds(actual) == _command_kinds(expected),
            "flow": _flows(actual) == _flows(expected),
            "arguments": _arguments(actual) == _arguments(expected),
            "next_state": (actual or {}).get("next_state") == expected["next_state"],
        }
        checks["exact"] = all(checks.values())
        correct.update(
            name for name, passed in checks.items()
            if passed and totals[name] > 0 and (
                name not in {"command", "flow", "arguments"}
                or expected_status == "RESOLVED"
            )
        )
        rule_totals[mapping_rule] += 1
        if checks["exact"]:
            rule_correct[mapping_rule] += 1
        if not checks["exact"]:
            failures.append({
                "case_id": case_id,
                "expected_status": expected_status,
                "actual_status": actual_status,
                "failed_dimensions": [
                    name for name, passed in checks.items() if not passed
                ],
            })

    extras = sorted(set(by_id).difference(known_ids))
    dimensions = {
        name: {
            "correct": correct[name],
            "total": totals[name],
            "rate": correct[name] / totals[name] if totals[name] else 0.0,
        }
        for name in ("status", "command", "flow", "arguments", "next_state", "exact")
    }
    return {
        "schema_version": "dialogpilot-public-command-score-v1",
        "case_count": len(cases),
        "prediction_count": len(predictions),
        "dimensions": dimensions,
        "status_confusion": {
            expected: dict(sorted(actual.items()))
            for expected, actual in sorted(status_confusion.items())
        },
        "mapping_rule_exact": {
            rule: {
                "correct": rule_correct[rule],
                "total": total,
                "rate": rule_correct[rule] / total,
            }
            for rule, total in sorted(rule_totals.items())
        },
        "macro_mapping_rule_exact": (
            sum(rule_correct[rule] / total for rule, total in rule_totals.items())
            / len(rule_totals)
            if rule_totals else 0.0
        ),
        "missing_prediction_count": sum(case_id not in by_id for case_id in known_ids),
        "extra_prediction_ids": extras,
        "failures": failures,
    }


def _commands(value: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    commands = (value or {}).get("commands") or []
    if not isinstance(commands, list):
        return []
    return [item for item in commands if isinstance(item, dict)]


def _command_kinds(value: Mapping[str, Any] | None) -> list[str]:
    return [str(item.get("kind")) for item in _commands(value)]


def _flows(value: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    return [item.get("flow") or {} for item in _commands(value)]


def _arguments(value: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    return [item.get("arguments") or {} for item in _commands(value)]
