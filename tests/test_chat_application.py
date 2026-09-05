"""HTTP adapter preserves authenticated commands and typed Target outcomes."""
import asyncio
import json

from api import main
from application.chat_contracts import Completed, Failed
from core.auth import Principal


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
