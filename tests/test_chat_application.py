"""Application/HTTP ownership contracts for the production chat chain."""
import asyncio
from types import SimpleNamespace

import pytest

from api import main
from application.chat_application import (
    ChatApplication,
    ChatCommand,
    ChatServices,
    Completed,
    Failed,
)
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
        rollout_manager=component,
    )


def test_chat_application_is_directly_callable_without_http(monkeypatch):
    app = ChatApplication(
        _ready_services(),
        SimpleNamespace(trace_id=lambda: "trace-direct"),
    )
    expected = Completed(response_id="response-1", response={"ok": True})

    async def handle_ready(command):
        assert command == ChatCommand(
            message="查询订单",
            user_id="user-1",
            conv_id="conversation-1",
            request_id="request-1",
        )
        return expected

    monkeypatch.setattr(app, "_handle_ready", handle_ready)
    assert asyncio.run(app.handle(ChatCommand(
        message="查询订单",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="request-1",
    ))) is expected


def test_chat_application_projects_unexpected_failure_to_typed_outcome(monkeypatch):
    app = ChatApplication(
        _ready_services(),
        SimpleNamespace(trace_id=lambda: "trace-failure"),
    )

    async def fail(_command):
        raise RuntimeError("provider secret must not cross the boundary")

    monkeypatch.setattr(app, "_handle_ready", fail)
    outcome = asyncio.run(app.handle(ChatCommand(message="hello", user_id="user-1")))
    assert outcome == Failed(
        code="chat_application_failed",
        retryable=False,
        correlation_id="trace-failure",
        safe_message="internal application failure: RuntimeError",
    )


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
            assert command == ChatCommand(
                message="查询订单",
                user_id="user-1",
                conv_id="conversation-1",
                request_id="request-1",
            )
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
    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.chat(
            main.ChatRequest(message="查询订单"),
            Principal(subject="user-1", scopes=frozenset({"chat"})),
        ))
    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "error": "response_selection_unavailable",
        "retryable": True,
        "correlation_id": "trace-1",
    }
