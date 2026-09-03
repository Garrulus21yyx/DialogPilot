"""The locked L0 runner proves a real chat path without inventing a score."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from application.chat_application import Completed, Failed
from application.default_flow_registry import command_primary_flow_registry
from application.structured_command_producer import CommandCompletionFailure
from core.llm_metrics import capture_llm_usage
from evaluation.command_primary_eval.locked_conversation import (
    LockedConversationTransportAdapter,
)
from evaluation.command_primary_eval.locked_l0_chat_runtime import (
    build_locked_l0_chat_runtime,
)
from evaluation.command_primary_eval.locked_l0_artifacts import write_locked_l0_run
from evaluation.command_primary_eval.locked_l0_clarification import (
    LiveProviderIdentity,
    load_locked_l0_clarification_cases,
    score_locked_l0_turn,
)


DATASET = Path("data/eval/dialogpilot-synthetic-contract-v1")
CASE_ID = "dp-product-09-a"


class StaticClarifyCompletion:
    provider_version = "fake-messages-v1"

    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def complete(self, *, system: str, input_json: str) -> str:
        assert system
        self.inputs.append(input_json)
        return ('{"status":"CLARIFY","commands":[],"clarification":'
                '{"reason":"MISSING_REFERENT","missing_dimensions":'
                '["product_or_media_reference"],"candidate_flow_ids":[]}}')


class StaticNoSupportedFlowCompletion:
    provider_version = "fake-messages-v1"

    async def complete(self, *, system: str, input_json: str) -> str:
        assert system and input_json
        return '{"status":"NO_SUPPORTED_FLOW","commands":[],"clarification":null}'


class FailingCompletion:
    provider_version = "fake-messages-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system: str, input_json: str) -> str:
        del system, input_json
        self.calls += 1
        raise CommandCompletionFailure("unavailable")


def test_locked_l0_slice_contains_twenty_one_turn_clarifications() -> None:
    cases = load_locked_l0_clarification_cases(DATASET)

    assert len(cases) == 20
    assert all(len(case["turns"]) == 1 for case in cases)
    assert all(not case["turns"][0]["attachment_refs"] for case in cases)


def test_static_output_proves_chat_chain_but_is_not_score_eligible(tmp_path) -> None:
    completion = StaticClarifyCompletion()
    runtime = build_locked_l0_chat_runtime(completion)
    transport = LockedConversationTransportAdapter(runtime.application, DATASET)

    async def execute():
        with capture_llm_usage() as usage:
            turns = await transport.run(CASE_ID)
        return turns, usage.summary()

    turns, usage = asyncio.run(execute())
    case = load_locked_l0_clarification_cases(DATASET, case_ids=(CASE_ID,))[0]
    prediction = score_locked_l0_turn(
        case=case,
        outcome=turns[0].outcome,
        chat_plan=runtime.planner.results[0],
        completion_call=runtime.completion.calls[0],
        usage=usage,
        provider=LiveProviderIdentity("fake", "fake", "f" * 64, "0", False),
        latency_ms=1.0,
        run_id="fake-run",
    )

    assert isinstance(turns[0].outcome, Completed)
    assert prediction["status"] == "PASS"
    assert prediction["score_eligible"] is False
    assert all(prediction["checks"].values())
    prompt_input = json.loads(completion.inputs[0])
    assert set(prompt_input) == {
        "message",
        "history",
        "state",
        "registry_fingerprint",
        "registry_commands",
        "encoder_candidates",
    }
    assert "expected" not in completion.inputs[0]

    provider = LiveProviderIdentity("fake", "fake", "f" * 64, "0", False)
    registry = command_primary_flow_registry(str(case["initial_state"]["tenant_id"]))
    report = write_locked_l0_run(
        dataset_root=DATASET,
        output_dir=tmp_path,
        run_id="fake-run",
        cases=(case,),
        predictions=(prediction,),
        provider=provider,
        completion_version=completion.provider_version,
        prompt_fingerprints=(runtime.completion.calls[0]["system_prompt_sha256"],),
        registry=registry,
    )
    assert report["run_status"] == "NOT_RUN"
    assert report["evaluation_role"] == "DEV_CONTRACT_DIAGNOSTIC"
    assert report["pass_rate"] is None
    assert report["cost"]["latency_ms"]["p95"] == 1.0
    assert report["check_pass_counts"]["clarification_terminal"] == 1
    assert {path.name for path in tmp_path.iterdir()} == {
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    }
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["runtime"]["entrypoint"] == "ChatApplication.handle"
    assert manifest["provider"]["real_provider"] is False
    assert manifest["runtime"]["flow_registry_fingerprint"] == registry.fingerprint


def test_no_supported_flow_stays_on_command_primary_policy_terminal() -> None:
    runtime = build_locked_l0_chat_runtime(StaticNoSupportedFlowCompletion())
    transport = LockedConversationTransportAdapter(runtime.application, DATASET)

    turns = asyncio.run(transport.run(CASE_ID))

    assert isinstance(turns[0].outcome, Completed)
    response = turns[0].outcome.response
    assert response["routing_disposition"] == "out_of_scope"
    assert response["routing_policy_trace"]["decision_source"] == "command_primary"
    assert response["intent_prediction_id"] == ""
    stages = {stage.stage: stage for stage in turns[0].outcome.stages}
    assert stages["intent"].status.value == "skipped"
    assert "rule_response" in stages["route_path_plan"].detail["required_components"]
    assert "agent_orchestrator" in stages["route_path_plan"].detail["forbidden_components"]
    assert stages["knowledge_retrieval"].detail["used"] is False


def test_provider_failure_is_typed_and_not_retried() -> None:
    completion = FailingCompletion()
    runtime = build_locked_l0_chat_runtime(completion)
    transport = LockedConversationTransportAdapter(runtime.application, DATASET)

    async def execute():
        with capture_llm_usage() as usage:
            turns = await transport.run(CASE_ID)
        return turns, usage.summary()

    turns, usage = asyncio.run(execute())
    case = load_locked_l0_clarification_cases(DATASET, case_ids=(CASE_ID,))[0]
    prediction = score_locked_l0_turn(
        case=case,
        outcome=turns[0].outcome,
        chat_plan=runtime.planner.results[0],
        completion_call=runtime.completion.calls[0],
        usage=usage,
        provider=LiveProviderIdentity("fake", "fake", "f" * 64, "0", False),
        latency_ms=1.0,
        run_id="failure-run",
    )

    assert completion.calls == 1
    assert isinstance(turns[0].outcome, Failed)
    assert turns[0].outcome.code == "command_primary_planning_failed"
    assert runtime.planner.results[0].planning.understanding.status.value == (
        "PROVIDER_FAILURE"
    )
    assert prediction["status"] == "PROVIDER_FAILURE"
    assert prediction["score_eligible"] is False


def test_serialized_live_usage_can_make_a_scored_turn_eligible() -> None:
    completion = StaticClarifyCompletion()
    runtime = build_locked_l0_chat_runtime(completion)
    transport = LockedConversationTransportAdapter(runtime.application, DATASET)
    turns = asyncio.run(transport.run(CASE_ID))
    case = load_locked_l0_clarification_cases(DATASET, case_ids=(CASE_ID,))[0]
    usage = {
        "total": {"calls": 1, "errors": 0, "input_tokens": 10, "output_tokens": 5},
        "calls": [{"model": "model-v1"}],
    }

    prediction = score_locked_l0_turn(
        case=case,
        outcome=turns[0].outcome,
        chat_plan=runtime.planner.results[0],
        completion_call=runtime.completion.calls[0],
        usage=usage,
        provider=LiveProviderIdentity("provider", "model-v1", "f" * 64, "1.0", True),
        latency_ms=1.0,
        run_id="serialized-usage-run",
    )

    assert prediction["score_eligible"] is True
