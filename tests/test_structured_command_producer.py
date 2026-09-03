from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from application.default_flow_registry import command_primary_flow_registry
from application.route_policy_v2 import RoutePolicy, RoutePolicyStatus
from application.structured_command_producer import (
    CommandCompletionFailure,
    StructuredLLMCommandProducer,
)
from application.turn_state import (
    PrincipalScope,
    StateAvailability,
    StateSourceStatus,
    TurnStateSnapshot,
)
from application.turn_understanding import (
    CommandKind,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
    fingerprint_message,
)
from core.model_policy import ModelProfile
from infrastructure.anthropic_command_completion import AnthropicCommandCompletion


class FakeCompletion:
    provider_version = "fake-completion-v1"

    def __init__(self, output: str | None = None, *, fail: bool = False):
        self.output = output
        self.fail = fail
        self.calls = []

    async def complete(self, *, system: str, input_json: str) -> str:
        self.calls.append((system, input_json))
        if self.fail:
            raise CommandCompletionFailure("provider unavailable")
        return self.output or ""


class FakeMessages:
    def __init__(self, output: str):
        self.output = output
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            id="message-1",
            content=[SimpleNamespace(type="text", text=self.output)],
            usage=SimpleNamespace(input_tokens=20, output_tokens=10),
        )


def _state() -> TurnStateSnapshot:
    return TurnStateSnapshot(
        "request-1",
        PrincipalScope("tenant-1", "user-1", "conversation-1"),
        None,
        (),
        None,
        (),
        (),
        (),
        (
            StateSourceStatus(
                "flow_state",
                StateAvailability.CURRENT,
                "flow-state-v1",
            ),
        ),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )


def _run(producer, message="退款通常多久到账？"):
    state = _state()
    return asyncio.run(
        producer.produce(
            message,
            fingerprint_message(message),
            state,
            command_primary_flow_registry(state.principal.tenant_id),
        )
    )


def test_anthropic_adapter_produces_a_registry_backed_knowledge_command() -> None:
    output = json.dumps(
        {
            "status": "RESOLVED",
            "commands": [
                {
                    "kind": "ANSWER_KNOWLEDGE",
                    "source_flow_instance_id": None,
                    "target_flow_id": None,
                    "target_flow_version": None,
                }
            ],
        }
    )
    messages = FakeMessages(output)
    completion = AnthropicCommandCompletion(
        SimpleNamespace(messages=messages),
        ModelProfile("claude-command-test"),
    )

    result = _run(StructuredLLMCommandProducer(completion))

    assert result.status is UnderstandingStatus.RESOLVED
    assert result.commands[0].kind is CommandKind.ANSWER_KNOWLEDGE
    assert result.commands[0].source is UnderstandingSource.LLM
    registry = command_primary_flow_registry("tenant-1")
    assert registry.action_for(result.commands[0]).action_id == "knowledge.answer"
    assert messages.requests[0]["model"] == "claude-command-test"
    input_payload = json.loads(messages.requests[0]["messages"][0]["content"])
    assert {
        (item["kind"], item["flow_id"], item["flow_version"])
        for item in input_payload["registry_commands"]
    } == {
        ("ANSWER_KNOWLEDGE", None, None),
        ("CONTINUE_FLOW", "refund_status", "v1"),
        ("START_FLOW", "media_text_read", "v1"),
    }


def test_structured_completion_can_request_clarification() -> None:
    completion = FakeCompletion(
        json.dumps(
            {
                "status": "CLARIFY",
                "commands": [],
            }
        )
    )

    result = _run(StructuredLLMCommandProducer(completion), "这个怎么办？")

    assert result == UnderstandingResult(
        UnderstandingStatus.CLARIFY,
        reason_code="LLM_CLARIFICATION_REQUIRED",
    )


def test_completion_transport_failure_is_typed() -> None:
    result = _run(StructuredLLMCommandProducer(FakeCompletion(fail=True)))

    assert result.status is UnderstandingStatus.PROVIDER_FAILURE
    assert result.reason_code == "COMMAND_COMPLETION_FAILED"


def test_invalid_completion_is_typed_and_route_policy_treats_it_as_failure() -> None:
    result = _run(StructuredLLMCommandProducer(FakeCompletion("not-json")))
    state = _state()
    accepted = RoutePolicy().accept(
        result,
        state,
        command_primary_flow_registry(state.principal.tenant_id),
    )

    assert result.status is UnderstandingStatus.INVALID_PROVIDER_OUTPUT
    assert result.reason_code == "COMMAND_OUTPUT_INVALID"
    assert accepted.status is RoutePolicyStatus.FAILURE
