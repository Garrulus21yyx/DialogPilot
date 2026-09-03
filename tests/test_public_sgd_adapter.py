from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.public_sgd.adapter import convert_sgd
from evaluation.public_sgd.contracts import SgdAdapterError, load_jsonl
from evaluation.public_sgd.scoring import score_predictions
from evaluation.public_sgd.selection import freeze_stratified_subset
from evaluation.public_sgd.validation import validate_frozen_benchmark
from scripts.adapt_sgd_command_dataset import main as adapt_main
from scripts.score_sgd_command_predictions import main as score_main


COMMIT = "a" * 40


def test_adapter_maps_only_source_authorized_command_transitions(tmp_path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "adapted"

    result = convert_sgd(
        source,
        output,
        source_commit=COMMIT,
        splits=("dev",),
    )

    cases = load_jsonl(output / "dev" / "cases.jsonl")
    by_rule = {case["provenance"]["mapping_rule"]: case for case in cases}
    assert set(by_rule) == {
        "SGD_REQUIRED_SLOT_REQUEST",
        "SGD_SERVICE_CALL_FIRST",
        "SGD_SERVICE_CALL_REPEAT",
        "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY",
    }
    assert by_rule["SGD_REQUIRED_SLOT_REQUEST"]["expected"] == {
        "commands": [],
        "missing_required_slots": ["city"],
        "next_state": {"active_flows": [], "pending_required_slots": []},
        "reason_code": "SGD_REQUIRED_SLOT_MISSING",
        "status": "CLARIFY",
    }
    started = by_rule["SGD_SERVICE_CALL_FIRST"]
    assert started["expected"]["commands"][0] == {
        "arguments": {"city": "Berlin"},
        "flow": {
            "flow_id": "sgd.Restaurants_1.FindRestaurants",
            "instance_id": None,
            "version": "sgd-v1",
        },
        "kind": "START_FLOW",
    }
    continued = by_rule["SGD_SERVICE_CALL_REPEAT"]
    assert continued["expected"]["commands"][0]["kind"] == "CONTINUE_FLOW"
    assert continued["input"]["current_state"]["active_flows"][0][
        "instance_id"
    ] == continued["expected"]["commands"][0]["flow"]["instance_id"]
    oos = by_rule["SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY"]
    assert oos["expected"]["status"] == "NO_SUPPORTED_FLOW"
    assert oos["expected"]["commands"] == []

    assert result.manifest["publication_status"] == "FROZEN_FULL_SPLITS"
    assert result.reports["dev"]["mapping_counts"] == {
        "SGD_REQUIRED_SLOT_REQUEST": 1,
        "SGD_SERVICE_CALL_FIRST": 1,
        "SGD_SERVICE_CALL_REPEAT": 1,
        "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY": 1,
    }
    assert json.loads((output / "registry.json").read_text())["flows"][0][
        "required_arguments"
    ] == ["city"]
    assert json.loads((output / "registry.json").read_text())["flows"][0][
        "argument_definitions"
    ] == [{
        "description": "city description",
        "name": "city",
        "possible_values": [],
    }]


def test_adapter_excludes_ambiguous_turns_with_typed_reason(tmp_path) -> None:
    source = _source(tmp_path, ambiguous=True)
    output = tmp_path / "adapted"

    convert_sgd(
        source,
        output,
        source_commit=COMMIT,
        splits=("dev",),
    )

    exclusions = load_jsonl(output / "dev" / "exclusions.jsonl")
    assert any(
        item["reason_code"] == "MULTI_OR_MISSING_USER_FRAME"
        for item in exclusions
    )


def test_adapter_rejects_mutable_or_overwriting_inputs(tmp_path) -> None:
    source = _source(tmp_path)
    with pytest.raises(SgdAdapterError, match="git SHA"):
        convert_sgd(source, tmp_path / "out", source_commit="main", splits=("dev",))

    output = tmp_path / "occupied"
    output.mkdir()
    (output / "existing").write_text("owned", encoding="utf-8")
    with pytest.raises(SgdAdapterError, match="must be empty"):
        convert_sgd(source, output, source_commit=COMMIT, splits=("dev",))


def test_scorer_checks_command_flow_arguments_state_and_cli(tmp_path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "adapted"
    convert_sgd(source, output, source_commit=COMMIT, splits=("dev",))
    cases = load_jsonl(output / "dev" / "cases.jsonl")
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("".join(
        json.dumps({
            "case_id": case["case_id"],
            "status": case["expected"]["status"],
            "commands": case["expected"]["commands"],
            "next_state": case["expected"]["next_state"],
        }, sort_keys=True) + "\n"
        for case in cases
    ), encoding="utf-8")

    report = score_predictions(output / "dev" / "cases.jsonl", predictions)
    assert report["dimensions"]["exact"]["rate"] == 1.0

    broken = list(load_jsonl(predictions))
    broken[0]["status"] = "NO_SUPPORTED_FLOW"
    predictions.write_text(
        "\n".join(json.dumps(row) for row in broken) + "\n", encoding="utf-8"
    )
    report = score_predictions(output / "dev" / "cases.jsonl", predictions)
    assert report["dimensions"]["status"]["correct"] == len(cases) - 1
    assert report["dimensions"]["exact"]["correct"] == len(cases) - 1

    score_output = tmp_path / "score.json"
    assert score_main([
        "--cases", str(output / "dev" / "cases.jsonl"),
        "--predictions", str(predictions),
        "--output", str(score_output),
    ]) == 0
    assert score_output.is_file()


def test_build_cli_marks_limited_runs_as_dev_subsets(tmp_path) -> None:
    source = _source(tmp_path)
    output = tmp_path / "adapted"
    assert adapt_main([
        "--source", str(source),
        "--source-commit", COMMIT,
        "--output", str(output),
        "--splits", "dev",
        "--max-dialogues", "2",
    ]) == 0
    assert json.loads((output / "manifest.json").read_text())[
        "publication_status"
    ] == "DEV_SUBSET"


def test_stratified_selection_is_balanced_and_reproducible(tmp_path) -> None:
    source = _source(tmp_path)
    pool = tmp_path / "pool"
    convert_sgd(source, pool, source_commit=COMMIT, splits=("dev",))
    selected = tmp_path / "selected"
    manifest = freeze_stratified_subset(
        pool,
        selected,
        per_rule={
            "SGD_REQUIRED_SLOT_REQUEST": 1,
            "SGD_SERVICE_CALL_FIRST": 1,
            "SGD_SERVICE_CALL_REPEAT": 1,
            "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY": 1,
        },
        seed="fixed",
    )

    assert manifest["publication_status"] == "FROZEN_STRATIFIED_ADAPTED_SPLITS"
    assert manifest["splits"]["dev"]["selected_mapping_counts"] == {
        "SGD_REQUIRED_SLOT_REQUEST": 1,
        "SGD_SERVICE_CALL_FIRST": 1,
        "SGD_SERVICE_CALL_REPEAT": 1,
        "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY": 1,
    }
    assert "not official SGD leaderboard" in (selected / "README.md").read_text()
    validation = validate_frozen_benchmark(selected)
    assert validation["valid"] is True
    assert validation["case_count"] == 4

    cases_path = selected / "dev" / "cases.jsonl"
    cases_path.write_text(cases_path.read_text() + "{}\n", encoding="utf-8")
    with pytest.raises(SgdAdapterError, match="checksum"):
        validate_frozen_benchmark(selected)


def _source(tmp_path: Path, *, ambiguous: bool = False) -> Path:
    root = tmp_path / "sgd"
    (root / "train").mkdir(parents=True)
    (root / "dev").mkdir()
    (root / "LICENSE.txt").write_text("CC BY-SA 4.0", encoding="utf-8")
    train_schema = [_service("Restaurants_1", "FindRestaurants")]
    dev_schema = [
        _service("Restaurants_1", "FindRestaurants"),
        _service("Flights_9", "FindFlight", required=("origin",)),
    ]
    _json(root / "train" / "schema.json", train_schema)
    _json(root / "dev" / "schema.json", dev_schema)

    supported_turns = [
        _user("Restaurants_1", "FindRestaurants", {}, "Find me a restaurant"),
        _system("Restaurants_1", requests=("city",)),
        _user(
            "Restaurants_1", "FindRestaurants", {"city": ["Berlin"]}, "In Berlin"
        ),
        _system(
            "Restaurants_1",
            service_call={"method": "FindRestaurants", "parameters": {"city": "Berlin"}},
        ),
        _user(
            "Restaurants_1", "FindRestaurants", {"city": ["Potsdam"]}, "Try Potsdam"
        ),
        _system(
            "Restaurants_1",
            service_call={"method": "FindRestaurants", "parameters": {"city": "Potsdam"}},
        ),
    ]
    unsupported_turns = [
        _user("Flights_9", "FindFlight", {}, "Find me a flight"),
        _system("Flights_9", requests=("origin",)),
    ]
    dialogues = [
        {"dialogue_id": "1_00001", "services": ["Restaurants_1"], "turns": supported_turns},
        {"dialogue_id": "1_00002", "services": ["Flights_9"], "turns": unsupported_turns},
    ]
    if ambiguous:
        first = _user("Restaurants_1", "FindRestaurants", {}, "ambiguous")
        first["frames"].append(dict(first["frames"][0]))
        dialogues.append({
            "dialogue_id": "1_00003",
            "services": ["Restaurants_1"],
            "turns": [first, _system("Restaurants_1", requests=("city",))],
        })
    _json(root / "dev" / "dialogues_001.json", dialogues)
    return root


def _service(service: str, intent: str, *, required=("city",)):
    return {
        "service_name": service,
        "description": service,
        "slots": [
            {
                "name": name,
                "description": f"{name} description",
                "is_categorical": False,
                "possible_values": [],
            }
            for name in required
        ],
        "intents": [{
            "name": intent,
            "description": f"{intent} description",
            "is_transactional": False,
            "required_slots": list(required),
            "optional_slots": {},
            "result_slots": [],
        }],
    }


def _user(service, intent, slots, utterance):
    return {
        "speaker": "USER",
        "utterance": utterance,
        "frames": [{
            "service": service,
            "actions": [],
            "slots": [],
            "state": {
                "active_intent": intent,
                "requested_slots": [],
                "slot_values": slots,
            },
        }],
    }


def _system(service, *, requests=(), service_call=None):
    frame = {
        "service": service,
        "actions": [
            {"act": "REQUEST", "slot": slot, "values": [], "canonical_values": []}
            for slot in requests
        ],
        "slots": [],
    }
    if service_call is not None:
        frame["service_call"] = service_call
    return {"speaker": "SYSTEM", "utterance": "system", "frames": [frame]}


def _json(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
