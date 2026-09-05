from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from evaluation.clarification_eval import score_clarification_dialogues
from evaluation.customer_service_command_gold import validate_command_gold
from evaluation.public_sgd.failure_attribution import FailureOwner, _first_failure


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
    )
    assert report == {
        "schema_version": "dialogpilot-customer-service-command-validation-v1",
        "status": "PASS",
        "counts": {"dev.jsonl": 8, "heldout.jsonl": 4},
        "heldout_consumed": False,
    }


@pytest.mark.parametrize("flow_id,allowed", [
    ("refund_status", True),
    ("refund_eligibility", True),
    ("media_text_read", True),
    ("execute_refund", False),
    ("order_lookup", False),
    ("unknown", False),
])
def test_archived_gold_scope_does_not_expand_with_current_capabilities(
    tmp_path, flow_id, allowed,
):
    manifest = {"registry_generation": "command-primary-read-only-v3", "files": {}}
    for name in ("dev.jsonl", "heldout.jsonl"):
        text = json.dumps({
            "case_id": name,
            "expected": {
                "status": "RESOLVED",
                "commands": [{"flow": {"flow_id": flow_id}}],
                "clarification": None,
            },
        }) + "\n"
        (tmp_path / name).write_text(text, encoding="utf-8")
        manifest["files"][name] = {"sha256": hashlib.sha256(text.encode()).hexdigest()}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if allowed:
        assert validate_command_gold(tmp_path)["status"] == "PASS"
        with (tmp_path / "dev.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with pytest.raises(ValueError, match="checksum"):
            validate_command_gold(tmp_path)
    else:
        with pytest.raises(ValueError, match="outside the Registry"):
            validate_command_gold(tmp_path)
    manifest["registry_generation"] = "current-target-not-the-archived-registry"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="generations differ"):
        validate_command_gold(tmp_path)
