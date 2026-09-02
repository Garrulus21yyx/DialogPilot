"""Application/HTTP ownership contracts for the production chat chain."""
import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

from api import main
from application.chat_application import (
    ChatApplication,
    ChatCommand,
    ChatServices,
    Completed,
    Failed,
    StageStatus,
    _publication_disposition,
)
from application.memory_projection import (
    MemoryProjectionResult,
    MemoryProjectionState,
    MemoryRetrievalOutcome,
)
from memory.conversation_memory import MemoryContext
from agents.request_shape_policy import RequestShapePolicy
from application.route_decision import (
    ComponentInvocation,
    ComponentStatus,
    RequiredAuthority,
    RouteDecision,
    RouteMode,
    RouteRisk,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel
from core.auth import Principal


def _ready_services() -> ChatServices:
    component = object()
    return ChatServices(
        orchestrator=component,
        memory=component,
        answer_verifier=component,
        ticket_service=component,
        response_delivery=component,
        context_assembler=component,
        bundle_registry=component,
        bundle_resolver=component,
    )


def test_routing_disposition_is_closed_at_publication_boundary():
    assert _publication_disposition("execute", approval_pending=False) == "normal"
    assert _publication_disposition("clarify", approval_pending=False) == "clarification"
    assert _publication_disposition("out_of_scope", approval_pending=False) == "out_of_scope"
    assert _publication_disposition("execute", approval_pending=True) == "approval"


def test_chat_application_is_directly_callable_without_http(monkeypatch):
    captured = {}

    class Recorder:
        @contextmanager
        def span(self, name, *, kind, attributes):
            captured.update(name=name, kind=kind, attributes=attributes)
            yield

    services = _ready_services()
    services = ChatServices(**{
        **services.__dict__,
        "trace_recorder": Recorder(),
    })
    app = ChatApplication(
        services,
        SimpleNamespace(trace_id=lambda: "trace-direct"),
    )
    expected = Completed(response_id="response-1", response={"ok": True})

    async def handle_ready(command, identity):
        assert command == ChatCommand(
            message="查询订单",
            user_id="user-1",
            conv_id="conversation-1",
            request_id="request-1",
        )
        assert identity.request_id == "request-1"
        assert identity.conversation_id == "conversation-1"
        return expected

    monkeypatch.setattr(app, "_handle_ready", handle_ready)
    assert asyncio.run(app.handle(ChatCommand(
        message="查询订单",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="request-1",
    ))) is expected
    assert captured["name"] == "application.chat"
    assert captured["attributes"]["request_id"] == "request-1"
    assert captured["attributes"]["invocation_key"].startswith("invocation:v1:")


def test_chat_application_projects_unexpected_failure_to_typed_outcome(monkeypatch):
    app = ChatApplication(
        _ready_services(),
        SimpleNamespace(trace_id=lambda: "trace-failure"),
    )

    async def fail(_command, _identity):
        raise RuntimeError("provider secret must not cross the boundary")

    monkeypatch.setattr(app, "_handle_ready", fail)
    outcome = asyncio.run(app.handle(ChatCommand(message="hello", user_id="user-1")))
    assert outcome == Failed(
        code="chat_application_failed",
        retryable=False,
        correlation_id="trace-failure",
        safe_message="internal application failure: RuntimeError",
    )


def test_unavailable_memory_projection_fails_before_intent_or_agent_work():
    class Memory:
        async def get_projection_result(self, *_args, **_kwargs):
            return MemoryProjectionResult(
                MemoryProjectionState.UNAVAILABLE,
                MemoryContext([], [], {}, "", []),
                0,
                {},
                MemoryRetrievalOutcome.UNAVAILABLE,
                reason_codes=("SOURCE_WATERMARK_UNAVAILABLE",),
            )

    class BundleResolver:
        def resolve(self, _subject):
            return SimpleNamespace(
                primary=SimpleNamespace(version="bundle-v1"),
                pinned_refs=None,
            )

    class MustNotRun:
        async def recognize_intent(self, *_args, **_kwargs):
            raise AssertionError("unavailable Memory must stop before inference")

    services = _ready_services()
    services = ChatServices(**{
        **services.__dict__,
        "memory": Memory(),
        "bundle_resolver": BundleResolver(),
        "orchestrator": MustNotRun(),
    })
    app = ChatApplication(
        services, SimpleNamespace(trace_id=lambda: "trace-memory-unavailable"),
    )
    outcome = asyncio.run(app.handle(ChatCommand(
        message="continue prior service", user_id="user-memory",
    )))
    assert isinstance(outcome, Failed)
    assert outcome.code == "memory_projection_unavailable"
    assert outcome.retryable is True
    assert outcome.stages[0].detail["state"] == "UNAVAILABLE"


def test_http_chat_is_a_thin_command_and_outcome_adapter(monkeypatch):
    response = main.ChatResponse(
        request_id="request-1",
        conv_id="conversation-1",
        response_id="response-1",
        response_seq=1,
        response="已完成",
        intent="order_query",
        agent_type="general",
        escalated=False,
        latency_ms=1.0,
        verification_status="pass",
        verified=True,
        grounded=True,
    )

    class FakeApplication:
        async def handle(self, command):
            assert command.message == "查询订单"
            assert command.user_id == "user-1"
            assert command.tenant_id == "default"
            assert command.conv_id == "conversation-1"
            assert command.request_id == "request-1"
            assert len(command.authorization_fingerprint) == 64
            return Completed(
                response_id=response.response_id,
                response=response.model_dump(),
            )

    monkeypatch.setattr(main, "_chat_application", lambda: FakeApplication())
    outcome = asyncio.run(main.chat(
        main.ChatRequest(
            message="查询订单",
            user_id="user-1",
            conv_id="conversation-1",
            request_id="request-1",
        ),
        Principal(subject="user-1", scopes=frozenset({"chat"})),
    ))
    assert outcome == response


def test_http_chat_maps_typed_retryable_failure(monkeypatch):
    class FakeApplication:
        async def handle(self, _command):
            return Failed(
                code="response_selection_unavailable",
                retryable=True,
                correlation_id="trace-1",
            )

    monkeypatch.setattr(main, "_chat_application", lambda: FakeApplication())
    response = asyncio.run(main.chat(
        main.ChatRequest(message="查询订单"),
        Principal(subject="user-1", scopes=frozenset({"chat"})),
    ))
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "outcome": "failed",
        "code": "response_selection_unavailable",
        "retryable": True,
        "correlation_id": "trace-1",
        "safe_message": "",
        "client_action": "retry_same_request",
        "retry_hint": "reuse_request_id",
    }


def test_chat_route_plan_is_the_canonical_immutable_contract():
    shape = RequestShapePolicy().decide(
        message="退款政策", intent=IntentCategory.QUERY, confidence=0.95,
        urgency=UrgencyLevel.LOW, entities={},
    )
    components = tuple(ComponentInvocation(
        name, ComponentStatus.SKIPPED, "TEST", "a" * 64, "test-v1",
    ) for name in ("intent_fusion", "domain_routing", "instance_selection"))
    route = RouteDecision(
        RouteMode.KNOWLEDGE_QA, "knowledge_query", 0.95,
        (RequiredAuthority.KNOWLEDGE,), RouteRisk.LOW, ("STATIC",), (), (),
        components, "router-v1", "a" * 64,
    )

    class Orchestrator:
        async def classify_request_shape(self, _request):
            return shape

        async def decide_route(self, _request, received):
            assert received is shape
            return route

    services = ChatServices(**{
        **_ready_services().__dict__,
        "orchestrator": Orchestrator(),
    })
    app = ChatApplication(services, SimpleNamespace())
    intent = SimpleNamespace(
        intent=IntentCategory.QUERY, intent_group="query",
        urgency=UrgencyLevel.LOW, confidence=0.95, entities={},
        classifier_fingerprint="intent-v1", input_fingerprint="a" * 64,
        source_scores={},
    )
    stages = []

    plan = asyncio.run(app._plan_route_path(
        command=ChatCommand(message="退款政策", user_id="u"),
        identity_metadata={"tenant_id": "default"},
        bundle=SimpleNamespace(version="bundle-v1"), intent_result=intent,
        user_id="u", conv_id="c", request_id="r", stages=stages,
    ))

    assert plan.route_decision is route
    assert plan.execution_contract.mode is RouteMode.KNOWLEDGE_QA
    assert [item.requirement_id for item in plan.requirements] == [
        "knowledge.active_source",
    ]
    assert stages[0].stage == "route_path_plan"
    assert stages[0].status is StageStatus.OK
