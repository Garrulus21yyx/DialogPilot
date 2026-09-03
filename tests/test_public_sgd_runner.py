from __future__ import annotations

import asyncio
import json

from application.command_argument_binding import ExplicitIdentifierArgumentBinder
from application.default_flow_registry import (
    REFUND_ELIGIBILITY,
    command_primary_flow_registry,
)
from application.route_policy_v2 import RoutePolicy
from application.structured_command_producer import StructuredLLMCommandProducer
from application.turn_understanding import (
    CommandArgument,
    CommandKind,
    CommandProposal,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
    fingerprint_message,
)
from evaluation.public_sgd.adapter import convert_sgd
from evaluation.public_sgd.runner import run_predictions
from evaluation.public_sgd.runtime import SgdBenchmarkDataset
from evaluation.public_sgd.scoring import score_predictions
from evaluation.public_sgd.selection import freeze_stratified_subset
from tests.test_public_sgd_adapter import COMMIT, _source


class FixtureCompletion:
    provider_version = "fixture-command-completion-v1"

    async def complete(self, *, system: str, input_json: str) -> str:
        payload = json.loads(input_json)
        message = payload["message"]
        if message == "Find me a restaurant":
            value = {"status": "CLARIFY", "commands": []}
        elif message == "Find me a flight":
            value = {"status": "NO_SUPPORTED_FLOW", "commands": []}
        else:
            city = "Berlin" if message == "In Berlin" else "Potsdam"
            active = payload["state"]["active_flows"]
            value = {
                "status": "RESOLVED",
                "commands": [{
                    "kind": "CONTINUE_FLOW" if active else "START_FLOW",
                    "source_flow_instance_id": (
                        active[0]["instance_id"] if active else None
                    ),
                    "target_flow_id": (
                        None if active else "sgd.Restaurants_1.FindRestaurants"
                    ),
                    "target_flow_version": None if active else "sgd-v1",
                    "arguments": {"city": city},
                }],
            }
        return json.dumps(value)


def test_runner_uses_production_parser_policy_and_state_transition(tmp_path) -> None:
    source = _source(tmp_path)
    pool = tmp_path / "pool"
    convert_sgd(source, pool, source_commit=COMMIT, splits=("dev",))
    selected = tmp_path / "selected"
    freeze_stratified_subset(
        pool,
        selected,
        per_rule={
            "SGD_REQUIRED_SLOT_REQUEST": 1,
            "SGD_SERVICE_CALL_FIRST": 1,
            "SGD_SERVICE_CALL_REPEAT": 1,
            "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY": 1,
        },
    )
    dataset = SgdBenchmarkDataset.load(selected, "dev")
    predictions = tmp_path / "predictions.jsonl"

    run = asyncio.run(run_predictions(
        dataset,
        FixtureCompletion(),
        predictions,
        concurrency=2,
    ))
    report = score_predictions(selected / "dev" / "cases.jsonl", predictions)

    assert run["case_count"] == 4
    assert run["new_prediction_count"] == 4
    assert report["dimensions"]["exact"]["rate"] == 1.0
    assert report["dimensions"]["arguments"] == {
        "correct": 2,
        "total": 2,
        "rate": 1.0,
    }
    resumed = asyncio.run(run_predictions(
        dataset,
        FixtureCompletion(),
        predictions,
        concurrency=2,
        resume=True,
    ))
    assert resumed["new_prediction_count"] == 0
    assert resumed["resumed_prediction_count"] == 4


def test_route_policy_rejects_model_argument_outside_registry(tmp_path) -> None:
    source = _source(tmp_path)
    pool = tmp_path / "pool"
    convert_sgd(source, pool, source_commit=COMMIT, splits=("dev",))
    selected = tmp_path / "selected"
    freeze_stratified_subset(
        pool,
        selected,
        per_rule={
            "SGD_REQUIRED_SLOT_REQUEST": 1,
            "SGD_SERVICE_CALL_FIRST": 1,
            "SGD_SERVICE_CALL_REPEAT": 1,
            "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY": 1,
        },
    )
    dataset = SgdBenchmarkDataset.load(selected, "dev")
    case = next(
        item for item in dataset.cases
        if item["provenance"]["mapping_rule"] == "SGD_SERVICE_CALL_FIRST"
    )
    message, state, history = dataset.materialize(case)

    class UnknownArgumentCompletion:
        provider_version = "bad-v1"

        async def complete(self, **_kwargs):
            return json.dumps({
                "status": "RESOLVED",
                "commands": [{
                    "kind": "START_FLOW",
                    "source_flow_instance_id": None,
                    "target_flow_id": "sgd.Restaurants_1.FindRestaurants",
                    "target_flow_version": "sgd-v1",
                    "arguments": {"city": "Berlin", "admin": True},
                }],
            })

    understanding = asyncio.run(
        StructuredLLMCommandProducer(UnknownArgumentCompletion()).produce(
            message,
            fingerprint_message(message),
            state,
            dataset.registry,
            history=history,
        )
    )
    try:
        RoutePolicy().accept(understanding, state, dataset.registry)
    except ValueError as exc:
        assert "unsupported arguments: admin" in str(exc)
    else:
        raise AssertionError("unknown model argument was accepted")


def test_explicit_identifier_binder_rejects_model_user_conflict() -> None:
    message = "订单号 DP-123 能退款吗？"
    proposal = CommandProposal(
        kind=CommandKind.START_FLOW,
        source=UnderstandingSource.LLM,
        message_fingerprint=fingerprint_message(message),
        producer_version="test-v1",
        evidence_refs=("message:test",),
        target_flow=REFUND_ELIGIBILITY,
        arguments=(CommandArgument.create("order_id", "MADE-UP"),),
    )
    result = ExplicitIdentifierArgumentBinder().bind(
        UnderstandingResult(UnderstandingStatus.RESOLVED, (proposal,)),
        message,
        command_primary_flow_registry("tenant-1"),
    )

    assert result.status is UnderstandingStatus.INVALID_PROVIDER_OUTPUT
    assert result.reason_code == "MODEL_ARGUMENT_CONTRADICTS_EXPLICIT_ID"
