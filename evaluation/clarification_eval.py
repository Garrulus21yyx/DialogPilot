"""Deterministic multi-turn scoring for structured clarification predictions."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def score_clarification_dialogues(
    cases: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    by_id = {str(row["case_id"]): row for row in predictions}
    required_total = clear_total = unnecessary = 0
    necessary = resolved = discriminating = oos_errors = added_turns = 0
    details = []
    for case in cases:
        prediction = by_id.get(str(case["case_id"]), {})
        first = prediction.get("first_turn") or {}
        second = prediction.get("second_turn") or {}
        expected_clarification = case["expected_clarification"]
        clarification_required = case["expected_first_status"] == "CLARIFY"
        is_clarify = first.get("status") == "CLARIFY"
        if clarification_required:
            required_total += 1
        else:
            clear_total += 1
        if is_clarify and clarification_required:
            necessary += 1
            added_turns += 1
        elif is_clarify:
            unnecessary += 1
            added_turns += 1
        if first.get("status") == "NO_SUPPORTED_FLOW":
            oos_errors += 1
        structure_matches = first.get("clarification") == expected_clarification
        expected_resolution = case["expected_resolution"]
        resolution_matches = all(
            second.get(name) == expected_resolution.get(name)
            for name in ("status", "kind", "flow_id")
        )
        if clarification_required and is_clarify and resolution_matches:
            resolved += 1
        expected_candidates = set(
            (expected_clarification or {}).get("candidate_flow_ids") or ()
        )
        chosen = second.get("flow_id")
        eliminates_candidate = (
            not expected_candidates
            or chosen in expected_candidates
        )
        if clarification_required and structure_matches and eliminates_candidate:
            discriminating += 1
        details.append({
            "case_id": case["case_id"],
            "clarification_required": is_clarify,
            "structure_matches": structure_matches,
            "one_turn_resolution": resolution_matches,
            "candidate_elimination": eliminates_candidate,
        })
    total = len(cases)
    return {
        "schema_version": "dialogpilot-clarification-dialogue-score-v1",
        "case_count": total,
        "necessary_clarification_recall": (
            necessary / required_total if required_total else 0.0
        ),
        "unnecessary_clarification_rate": (
            unnecessary / clear_total if clear_total else 0.0
        ),
        "one_clarification_resolution_rate": (
            resolved / required_total if required_total else 0.0
        ),
        "discriminating_question_rate": (
            discriminating / required_total if required_total else 0.0
        ),
        "oos_misclassification_rate": (
            oos_errors / required_total if required_total else 0.0
        ),
        "average_added_turns": added_turns / total if total else 0.0,
        "details": details,
    }
