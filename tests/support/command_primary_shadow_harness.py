"""Paired real-chat harness for the structured command shadow contract."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents.agent_orchestrator import OrchestratorResult
from agents.orchestration_contracts import AgentType
from agents.request_shape_policy import RequestShapePolicy
from application.active_case import (
    ActiveCaseContextView,
    ActiveCaseProjection,
    ActiveCaseSelection,
    ActiveCaseState,
)
from application.chat_application import (
    ChatApplication,
    ChatCommand,
    ChatOperations,
    ChatServices,
    Completed,
)
from application.flow_state import FlowStateAggregate
from application.route_decision import (
    ComponentInvocation,
    ComponentStatus,
    RouteDecision,
    RouteMode,
    RouteRisk,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel
from core.model_policy import ModelProfile
from infrastructure import command_primary_runtime
from infrastructure.command_primary_runtime import build_command_primary_chat_planner
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)


@dataclass(frozen=True)
class ShadowComparison:
    off: Completed
    shadow: Completed
    counters: dict[str, dict[str, Any]]
    provider_calls: int
    flow_version: int
    flow_commit_calls: int


class _FlowState:
    def __init__(self) -> None:
        self.current = None
        self.compare_and_set_calls = 0

    def load(self, principal):
        if self.current is None:
            self.current = FlowStateAggregate.empty(principal, deletion_epoch=0)
        return self.current

    def compare_and_set(self, *_args):
        self.compare_and_set_calls += 1
        raise AssertionError("shadow must not commit flow state")


class _Messages:
    def __init__(self) -> None:
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        command = {
            "kind": "START_FLOW",
            "source_flow_instance_id": None,
            "target_flow_id": "refund_eligibility",
            "target_flow_version": "v1",
            "arguments": {},
        }
        return SimpleNamespace(
            id="shadow-command",
            content=[
                SimpleNamespace(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "RESOLVED",
                            "commands": [command],
                        }
                    ),
                )
            ],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


def run_structured_shadow_comparison() -> ShadowComparison:
    counters = {
        mode: {"intent": 0, "run": 0, "delivery": []} for mode in ("off", "shadow")
    }
    flow_state = _FlowState()
    messages = _Messages()
    with patch.object(
        command_primary_runtime,
        "PostgresFlowStateStore",
        lambda _pool: flow_state,
    ):
        off_app = _application("off", counters, messages)
        shadow_app = _application("shadow", counters, messages)
    command = ChatCommand(
        "订单 order-1 能否申请退款？",
        "user-1",
        "tenant-1",
        "conversation-1",
        "request-1",
    )
    off = asyncio.run(off_app.handle(command))
    shadow = asyncio.run(shadow_app.handle(command))
    if not isinstance(off, Completed) or not isinstance(shadow, Completed):
        raise AssertionError("paired shadow chat did not complete")
    return ShadowComparison(
        off,
        shadow,
        counters,
        len(messages.requests),
        flow_state.current.aggregate.version,
        flow_state.compare_and_set_calls,
    )


def _application(mode: str, counters: dict[str, dict[str, Any]], messages: _Messages):
    async def recognize(*_args, **_kwargs):
        counters[mode]["intent"] += 1
        return SimpleNamespace(
            intent=IntentCategory.GREETING,
            intent_group="greeting",
            urgency=UrgencyLevel.LOW,
            confidence=0.99,
            entities={},
            source_scores={},
            classifier_fingerprint="legacy-v1",
            input_fingerprint="a" * 64,
        )

    async def classify(request):
        return RequestShapePolicy().decide(
            message=request.message,
            intent=request.intent,
            confidence=request.intent_confidence,
            urgency=request.urgency,
            entities=request.entities,
        )

    async def route(_request, shape):
        trace = tuple(
            ComponentInvocation(
                name,
                ComponentStatus.SKIPPED,
                "LEGACY_DIRECT",
                shape.input_fingerprint,
                "legacy-route-v1",
            )
            for name in ("intent_fusion", "domain_routing", "instance_selection")
        )
        return RouteDecision(
            RouteMode.DIRECT,
            "greeting",
            0.99,
            (),
            RouteRisk.LOW,
            ("GREETING_INTENT",),
            (),
            (),
            trace,
            "legacy-route-v1",
            shape.input_fingerprint,
        )

    async def run(request):
        counters[mode]["run"] += 1
        return OrchestratorResult(
            request.request_id,
            "你好，我能为你做什么？",
            AgentType.GENERAL,
            IntentCategory.GREETING,
            latency_ms=1.0,
            agent_types=[AgentType.GENERAL],
            primary_agent=AgentType.GENERAL,
            routing_reason="legacy greeting",
            routing_confidence=0.99,
            coverage={"complete": True},
        )

    def deliver(**kwargs):
        counters[mode]["delivery"].append(kwargs)
        return SimpleNamespace(
            response_id="legacy-response",
            seq=1,
            status=SimpleNamespace(value="selected"),
        )

    orchestrator = SimpleNamespace(
        recognize_intent=recognize,
        classify_request_shape=classify,
        decide_route=route,
        run=run,
    )
    planner = build_command_primary_chat_planner(
        orchestrator,
        {"COMMAND_PRIMARY_MODE": mode},
        postgres_pool=object(),
        command_completion_client=SimpleNamespace(messages=messages),
        command_model_profile=ModelProfile("command-router-test"),
    )
    verification = VerificationResult(
        VerificationStatus.PASS,
        True,
        False,
        "legacy",
        VerificationReasonCode.PASSED,
    )
    services = ChatServices(
        orchestrator=orchestrator,
        memory=SimpleNamespace(get_context=_context, add_messages=_done),
        answer_verifier=object(),
        ticket_service=object(),
        response_delivery=SimpleNamespace(select_response=deliver),
        context_assembler=SimpleNamespace(
            assemble=lambda **_kwargs: SimpleNamespace(system_context=""),
        ),
        bundle_registry=object(),
        bundle_resolver=SimpleNamespace(
            resolve=lambda _user: SimpleNamespace(
                primary=SimpleNamespace(version="bundle-v1"),
                pinned_refs=None,
            )
        ),
        command_primary_chat_planner=planner,
    )
    operations = ChatOperations(
        active_ticket_context=_active_case,
        build_knowledge_context=_unexpected,
        capture_badcases=_done,
        handoff_priority=lambda *_args: None,
        policy_terminal_verification=lambda _result: verification,
        publish_candidate=lambda candidate, _check: candidate,
        public_agent_outcomes=lambda outcomes: outcomes,
        record_intent_prediction=_prediction,
        select_publication_candidate=(
            lambda candidate, _evidence, **_kwargs: (candidate, False)
        ),
        trace_id=lambda: "shadow-test-trace",
        verify_for_publication=_unexpected,
    )
    return ChatApplication(services, operations)


async def _context(*_args, **_kwargs):
    return SimpleNamespace(
        recent_messages=[], retrieval_hits=[], to_sections=lambda: []
    )


async def _active_case(*_args, **_kwargs):
    return ActiveCaseContextView(
        ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
        ActiveCaseSelection((), ()),
    )


async def _prediction(*_args, **_kwargs):
    return SimpleNamespace(prediction_id="legacy-prediction")


async def _done(*_args, **_kwargs):
    return None


async def _unexpected(*_args, **_kwargs):
    raise AssertionError("unexpected capability invocation")
