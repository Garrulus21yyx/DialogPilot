from __future__ import annotations

import json
from pathlib import Path

import pytest

from application.default_flow_registry import command_primary_flow_registry
from application.turn_understanding import (
    ClarificationDecision,
    ClarificationReason,
    UnderstandingError,
    UnderstandingResult,
    UnderstandingStatus,
)
from evaluation.clarification_eval import score_clarification_dialogues
from evaluation.customer_service_command_gold import validate_command_gold
from evaluation.public_sgd.failure_attribution import FailureOwner, _first_failure


def test_clarification_contract_is_closed_and_typed() -> None:
    with pytest.raises(UnderstandingError):
        UnderstandingResult(UnderstandingStatus.CLARIFY, reason_code="missing")
    with pytest.raises(UnderstandingError):
        ClarificationDecision(
            ClarificationReason.MULTIPLE_SUPPORTED_FLOWS,
            ("request_goal",),
            ("refund_status",),
        )


def test_failure_attribution_selects_only_earliest_owner() -> None:
    expected = {
        "status": "RESOLVED",
        "commands": [{
            "kind": "START_FLOW",
            "flow": {"flow_id": "a"},
            "arguments": {"id": "1"},
        }],
        "next_state": {},
    }
    actual = {
        "status": "RESOLVED",
        "commands": [{
            "kind": "START_FLOW",
            "flow": {"flow_id": "b"},
            "arguments": {"id": "2"},
        }],
        "next_state": {"changed": True},
    }
    assert _first_failure(expected, actual) is FailureOwner.FLOW_SELECTION


def test_clarification_dev_contract_scores_multi_turn_resolution() -> None:
    cases = [
        json.loads(line)
        for line in Path(
            "data/eval/customer-service-command-gold-v1/clarification-dev.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    predictions = [
        {
            "case_id": case["case_id"],
            "first_turn": {
                "status": case["expected_first_status"],
                "clarification": case["expected_clarification"],
            },
            "second_turn": case["expected_resolution"],
        }
        for case in cases
    ]
    score = score_clarification_dialogues(cases, predictions)
    assert score["one_clarification_resolution_rate"] == 1.0
    assert score["discriminating_question_rate"] == 1.0
    assert score["unnecessary_clarification_rate"] == 0.0
    assert score["oos_misclassification_rate"] == 0.0


def test_customer_service_command_gold_is_frozen_and_registry_bounded() -> None:
    report = validate_command_gold(
        Path("data/eval/customer-service-command-gold-v1"),
        command_primary_flow_registry("tenant-1"),
    )
    assert report == {
        "schema_version": "dialogpilot-customer-service-command-validation-v1",
        "status": "PASS",
        "counts": {"dev.jsonl": 8, "heldout.jsonl": 4},
        "heldout_consumed": False,
    }
