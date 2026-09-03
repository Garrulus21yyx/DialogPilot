"""Attribute each command benchmark failure to its earliest owning boundary."""
from __future__ import annotations

from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from evaluation.public_sgd.contracts import load_jsonl


class FailureOwner(str, Enum):
    PASS = "PASS"
    MISSING_PREDICTION = "MISSING_PREDICTION"
    PROVIDER_OUTPUT = "PROVIDER_OUTPUT"
    TERMINAL_DECISION = "TERMINAL_DECISION"
    COMMAND_KIND = "COMMAND_KIND"
    FLOW_SELECTION = "FLOW_SELECTION"
    ARGUMENT_BINDING = "ARGUMENT_BINDING"
    STATE_PROJECTION = "STATE_PROJECTION"


def attribute_failures(
    cases_path: Path,
    predictions_path: Path,
) -> Mapping[str, Any]:
    cases = load_jsonl(cases_path)
    predictions = {
        str(row["case_id"]): row for row in load_jsonl(predictions_path)
    }
    counts: Counter[str] = Counter()
    rows = []
    for case in cases:
        actual = predictions.get(str(case["case_id"]))
        owner = _first_failure(case["expected"], actual)
        counts[owner.value] += 1
        rows.append({
            "case_id": case["case_id"],
            "owner": owner.value,
            "expected_status": case["expected"]["status"],
            "actual_status": (actual or {}).get("status", "MISSING"),
        })
    failed = len(cases) - counts[FailureOwner.PASS.value]
    return {
        "schema_version": "dialogpilot-command-failure-attribution-v1",
        "policy": "EARLIEST_AUTHORITATIVE_BOUNDARY",
        "case_count": len(cases),
        "failed_count": failed,
        "counts": dict(sorted(counts.items())),
        "failure_rates": {
            name: count / failed if failed else 0.0
            for name, count in sorted(counts.items())
            if name != FailureOwner.PASS.value
        },
        "cases": rows,
    }


def _first_failure(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any] | None,
) -> FailureOwner:
    if actual is None:
        return FailureOwner.MISSING_PREDICTION
    actual_status = str(actual.get("status") or "")
    if actual_status in {"PROVIDER_FAILURE", "INVALID_PROVIDER_OUTPUT"}:
        return FailureOwner.PROVIDER_OUTPUT
    if actual_status != str(expected["status"]):
        return FailureOwner.TERMINAL_DECISION
    expected_commands = expected.get("commands") or []
    actual_commands = actual.get("commands") or []
    if [item.get("kind") for item in actual_commands] != [
        item.get("kind") for item in expected_commands
    ]:
        return FailureOwner.COMMAND_KIND
    if [item.get("flow") for item in actual_commands] != [
        item.get("flow") for item in expected_commands
    ]:
        return FailureOwner.FLOW_SELECTION
    if [item.get("arguments") or {} for item in actual_commands] != [
        item.get("arguments") or {} for item in expected_commands
    ]:
        return FailureOwner.ARGUMENT_BINDING
    if actual.get("next_state") != expected.get("next_state"):
        return FailureOwner.STATE_PROJECTION
    return FailureOwner.PASS
