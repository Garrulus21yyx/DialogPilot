"""Integrity and state-machine validation for frozen SGD adaptations."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from evaluation.public_sgd.adapter import CASE_SCHEMA_VERSION
from evaluation.public_sgd.contracts import (
    SgdAdapterError,
    load_json,
    load_jsonl,
    sha256_file,
)


def validate_frozen_benchmark(root: Path) -> Mapping[str, Any]:
    manifest = load_json(root / "manifest.json")
    if manifest.get("publication_status") != "FROZEN_STRATIFIED_ADAPTED_SPLITS":
        raise SgdAdapterError("benchmark is not a frozen stratified adaptation")
    if sha256_file(root / "registry.json") != manifest.get("registry_sha256"):
        raise SgdAdapterError("registry checksum mismatch")
    reports: dict[str, Mapping[str, Any]] = {}
    all_case_ids: set[str] = set()
    for split, expected_report in manifest.get("splits", {}).items():
        split_dir = root / split
        cases_path = split_dir / "cases.jsonl"
        conversations_path = split_dir / "conversations.jsonl"
        if sha256_file(cases_path) != expected_report.get("cases_sha256"):
            raise SgdAdapterError(f"case checksum mismatch: {split}")
        if sha256_file(conversations_path) != expected_report.get(
            "conversations_sha256"
        ):
            raise SgdAdapterError(f"conversation checksum mismatch: {split}")
        cases = load_jsonl(cases_path)
        conversations = load_jsonl(conversations_path)
        if len(cases) != expected_report.get("case_count"):
            raise SgdAdapterError(f"case count mismatch: {split}")
        if len(conversations) != expected_report.get("conversation_count"):
            raise SgdAdapterError(f"conversation count mismatch: {split}")
        by_dialogue = {str(row["dialogue_id"]): row for row in conversations}
        if len(by_dialogue) != len(conversations):
            raise SgdAdapterError(f"duplicate conversation id: {split}")
        rule_counts: dict[str, int] = {}
        for case in cases:
            _validate_case(case, split, by_dialogue)
            case_id = str(case["case_id"])
            if case_id in all_case_ids:
                raise SgdAdapterError("case ids must be globally unique")
            all_case_ids.add(case_id)
            rule = str(case["provenance"]["mapping_rule"])
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
        if rule_counts != expected_report.get("selected_mapping_counts"):
            raise SgdAdapterError(f"mapping rule counts mismatch: {split}")
        reports[split] = {
            "case_count": len(cases),
            "conversation_count": len(conversations),
            "mapping_counts": rule_counts,
        }
    if not reports:
        raise SgdAdapterError("benchmark contains no splits")
    return {
        "schema_version": "dialogpilot-public-command-validation-v1",
        "valid": True,
        "dataset_id": manifest["dataset_id"],
        "case_count": len(all_case_ids),
        "splits": reports,
    }


def _validate_case(
    case: Mapping[str, Any],
    split: str,
    conversations: Mapping[str, Mapping[str, Any]],
) -> None:
    if case.get("schema_version") != CASE_SCHEMA_VERSION or case.get("split") != split:
        raise SgdAdapterError("case schema/split mismatch")
    history = case["input"]["history"]
    dialogue_id = str(history["dialogue_id"])
    conversation = conversations.get(dialogue_id)
    if conversation is None:
        raise SgdAdapterError("case history conversation is unavailable")
    turn_index = int(history["exclusive_end_turn"])
    turns = conversation["turns"]
    if not 0 <= turn_index < len(turns):
        raise SgdAdapterError("case history turn is outside conversation")
    turn = turns[turn_index]
    if turn.get("speaker") != "USER" or turn.get("utterance") != case["input"]["message"]:
        raise SgdAdapterError("case message does not bind the source user turn")

    expected = case["expected"]
    status = expected["status"]
    commands = expected["commands"]
    rule = case["provenance"]["mapping_rule"]
    if status == "RESOLVED":
        if len(commands) != 1 or commands[0]["kind"] not in {
            "START_FLOW", "CONTINUE_FLOW"
        }:
            raise SgdAdapterError("resolved case command algebra is invalid")
        active = case["input"]["current_state"]["active_flows"]
        instance = commands[0]["flow"]["instance_id"]
        if commands[0]["kind"] == "START_FLOW" and (active or instance is not None):
            raise SgdAdapterError("START_FLOW must create a new instance")
        if commands[0]["kind"] == "CONTINUE_FLOW" and (
            len(active) != 1 or active[0]["instance_id"] != instance
        ):
            raise SgdAdapterError("CONTINUE_FLOW must bind the active instance")
    elif status == "CLARIFY":
        if commands or not expected["missing_required_slots"] or rule != "SGD_REQUIRED_SLOT_REQUEST":
            raise SgdAdapterError("CLARIFY must cite missing required slots")
    elif status == "NO_SUPPORTED_FLOW":
        if commands or rule != "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY":
            raise SgdAdapterError("OOS must be owned by the train registry boundary")
    else:
        raise SgdAdapterError("unsupported expected status")
