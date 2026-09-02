"""M1-T05 HTTP adapters remain scoped, thin and read-only."""
from fastapi.testclient import TestClient

from api import main
from application.admission_contract import AdmissionStatus, ExecutionStatus
from application.conversation_query import (
    ConversationCloseResult,
    InvocationStatusView,
    ProjectionProgressView,
    TranscriptTurnView,
)
from application.delivery_contract import DeliveryStatusV1
from core.auth import Principal


class QueryDouble:
    def __init__(self):
        self.calls = []
        self.caught_up = False

    def list_turns(self, **kwargs):
        self.calls.append(("turns", kwargs))
        return (
            TranscriptTurnView(2, "assistant", "safe answer", "time", "request"),
        )

    def invocation_status(self, invocation_key, **kwargs):
        self.calls.append(("invocation", {"invocation_key": invocation_key, **kwargs}))
        return InvocationStatusView(
            invocation_key=invocation_key,
            workflow_run_id="workflow-1",
            admission_status=AdmissionStatus.EXECUTION_BOUND,
            execution_status=ExecutionStatus.COMPLETED,
            runtime={"runtime_kind": "compat", "runtime_status": "COMPLETED"},
            pending_signal=None,
            final_response={"response_id": "response-1", "response": "answer"},
            delivery_status=DeliveryStatusV1.SELECTED,
            ticket=None,
        )

    def projection_progress(self, **kwargs):
        self.calls.append(("progress", kwargs))
        return ProjectionProgressView(
            target_event_seq=4,
            watermarks={
                "working_window": 4 if self.caught_up else 3,
                "thread_summary": 4,
                "fact_extraction": 4,
            },
            caught_up=self.caught_up,
        )

    def close_conversation(self, **kwargs):
        self.calls.append(("close", kwargs))
        return ConversationCloseResult("close-1", False, kwargs["created_at"])


def _client(monkeypatch, query):
    monkeypatch.setattr(main, "_conversation_query", query)
    main.app.dependency_overrides[main.get_principal] = lambda: Principal(
        subject="user-api", scopes=frozenset({"chat"}),
    )
    return TestClient(main.app)


def test_turn_and_invocation_endpoints_only_map_application_read_models(monkeypatch):
    query = QueryDouble()
    client = _client(monkeypatch, query)
    try:
        turns = client.get(
            "/conversations/conversation-api/turns",
            params={"after_seq": 1, "limit": 10},
        )
        invocation = client.get("/invocations/invocation-api")
    finally:
        main.app.dependency_overrides.clear()
    assert turns.status_code == 200
    assert turns.json() == {
        "conv_id": "conversation-api",
        "turns": [{
            "seq": 2, "role": "assistant", "content": "safe answer",
            "created_at": "time", "request_id": "request",
        }],
        "last_seq": 2,
    }
    assert invocation.status_code == 200
    assert invocation.json()["execution_status"] == "COMPLETED"
    assert invocation.json()["delivery_status"] == "SELECTED"
    assert query.calls[0][1]["user_id"] == "user-api"
    assert query.calls[0][1]["tenant_id"] == "default"


def test_finalize_is_projection_wait_only_and_close_is_separate_command(monkeypatch):
    query = QueryDouble()
    client = _client(monkeypatch, query)
    try:
        pending = client.post("/conversations/conversation-api/finalize")
        query.caught_up = True
        caught_up = client.post("/conversations/conversation-api/finalize")
        closed = client.post(
            "/conversations/conversation-api/close",
            json={"reason_code": "user_closed"},
        )
    finally:
        main.app.dependency_overrides.clear()
    assert pending.status_code == 202
    assert pending.json()["caught_up"] is False
    assert pending.json()["deprecated"] is True
    assert caught_up.status_code == 200
    assert caught_up.json()["caught_up"] is True
    assert closed.status_code == 200
    assert closed.json()["close_id"] == "close-1"
    assert [name for name, _ in query.calls] == ["progress", "progress", "close"]
