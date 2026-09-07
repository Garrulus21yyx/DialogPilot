"""Official adapter diagnostics preserve the production protocol unchanged."""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
import pytest

pytest.importorskip("tau2")
from evaluation.tau3_full_adapter import ObservedVerifier, ModelDiagnostics, Tau3TargetAgent


def test_verifier_diagnostics_preserve_positional_request_and_verdict():
    @dataclass
    class Verdict:
        grounded: bool = False
    verdict = Verdict()
    calls = []
    class Verifier:
        async def verify(self, *args, **kwargs):
            calls.append((args, kwargs))
            return verdict
    trace = []
    result = asyncio.run(ObservedVerifier(Verifier(), trace).verify("question", "answer", context="context"))
    assert result is verdict
    assert calls == [(("question", "answer"), {"context": "context"})]
    assert trace == [{"verification": {"grounded": False}, "candidate": "answer"}]


def test_model_diagnostics_record_protocol_not_content():
    trace = []
    message = SimpleNamespace(response_metadata={"stop_reason": "max_tokens"},
        usage_metadata={"output_tokens": 1024}, tool_calls=[], invalid_tool_calls=[],
        content="private working content")
    ModelDiagnostics(trace).on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=message)]]))
    assert trace[0]["model_response"]["stop_reason"] == "max_tokens"
    assert "private working content" not in str(trace)


@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens", "tool_use"])
def test_visible_diagnostics_preserve_response_and_binding_without_reasoning(stop_reason):
    from uuid import uuid4
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import LLMResult, ChatGeneration
    trace = []
    observer = ModelDiagnostics(trace, capture_content=True)
    run_id, parent_id = uuid4(), uuid4()
    observer.on_chat_model_start({}, [], run_id=run_id, parent_run_id=parent_id,
                                 metadata={"work_item_id": "w1", "revision": 2, "user_id": "private-user"})
    message = AIMessage(content=[
        {"type": "thinking", "thinking": "hidden reasoning must not escape"},
        {"type": "text", "text": "Refund $13.46. Email me at test@example.com; password=secret-value"},
    ], response_metadata={"stop_reason": stop_reason}, tool_calls=[{
        "name": "lookup", "id": "tc1", "args": {"query": "catalog", "api_key": "private-key"},
    }])
    observer.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)
    result = trace[-1]["model_response"]
    assert result["run_id"] == str(run_id)
    assert result["parent_run_id"] == str(parent_id)
    assert result["revision"] == 2
    assert result["visible_text"].startswith("Refund $13.46.")
    assert result["tool_calls"][0]["id"] == "tc1"
    assert result["tool_calls"][0]["args"]["api_key"] == "[REDACTED]"
    for secret in ("hidden reasoning", "test@example.com", "secret-value", "private-key", "private-user"):
        assert secret not in str(trace)
    assert observer._calls == {}


def test_parallel_diagnostic_events_keep_per_call_identity_on_error():
    from uuid import uuid4
    from langchain_core.messages import ToolMessage
    trace = []
    observer = ModelDiagnostics(trace, capture_content=True)
    one, two = uuid4(), uuid4()
    for run_id, work in ((one, "one"), (two, "two")):
        observer.on_tool_start({"name": "lookup"}, "", run_id=run_id,
                               metadata={"work_item_id": work}, inputs={"query": work})
    observer.on_tool_error(ValueError("password=private-value"), run_id=two)
    observer.on_tool_end(ToolMessage(content="found", tool_call_id="tc1"), run_id=one)
    assert trace[-2]["tool_error"]["work_item_id"] == "two"
    assert trace[-1]["tool_response"]["work_item_id"] == "one"
    assert trace[-1]["tool_response"]["tool_call_id"] == "tc1"
    assert "private-value" not in str(trace)
    assert observer._calls == {}


@pytest.mark.parametrize("structured", [True, False])
def test_framework_callback_covers_real_model_tool_loop_without_changing_result(structured):
    from langchain_core.messages import AIMessage
    from tests.test_target_framework_agent import ScriptedToolModel, _manager, _context
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    from application.agent_result import AgentResultStatus
    trace, calls = [], []
    observer = ModelDiagnostics(trace, capture_content=True)
    class PlainModel(ScriptedToolModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model_type = ScriptedToolModel if structured else PlainModel
    model = model_type(callbacks=[observer], responses=[
        AIMessage(content="", tool_calls=[{"name": "catalog_search", "id": "read1", "args": {"query": "model"}}]),
        AIMessage(content="Found PX-200."),
    ])
    agent = TargetFrameworkAgent(model, _manager(calls),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Use tools.",
        callbacks=(observer,))
    result = asyncio.run(agent(_context()))
    assert result.status is (AgentResultStatus.SUCCEEDED if structured else AgentResultStatus.TERMINAL_FAILURE)
    responses = [event["model_response"] for event in trace if "model_response" in event]
    assert len(responses) == 2
    if not structured:
        assert result.reason_code == "DOMAIN_OUTCOME_MISSING"
        assert responses[-1]["visible_text"] == "Found PX-200."
    assert all(row["work_item_id"] == "product-work-1" and row["invocation_key"] == "invocation-a"
               and row["parent_run_id"] for row in responses)
    tools = [event["tool_response"] for event in trace if "tool_response" in event]
    assert len(tools) == 1 and tools[0]["tool_call_id"] == "read1"
    assert "PX-200" in tools[0]["visible_text"]
    assert len(calls) == 1


def test_model_error_diagnostics_release_binding_and_keep_safe_failure_detail():
    trace = []
    observer = ModelDiagnostics(trace, capture_content=True)
    observer.on_chat_model_start({}, [], run_id="call1", metadata={"work_item_id": "work1"})
    observer.on_llm_error(TimeoutError("endpoint failed; token=private-token"), run_id="call1")
    error = trace[-1]["model_error"]
    assert error["error_type"] == "TimeoutError"
    assert error["work_item_id"] == "work1"
    assert error["message"].startswith("endpoint failed")
    assert "private-token" not in str(trace)
    assert not observer._calls


def test_ambiguous_approval_remains_user_input_without_grant():
    from unittest.mock import AsyncMock
    from application.chat_contracts import Completed
    calls = []
    async def handle(command):
        calls.append(command)
        return Completed("reply", {"response": "The operation still requires your confirmation."})
    async def run():
        agent = Tau3TargetAgent(SimpleNamespace(get_tools=lambda: [], get_policy=lambda: "policy"),
                               loop=asyncio.get_running_loop())
        agent.states = SimpleNamespace(load=lambda *args: SimpleNamespace(pending_approval=SimpleNamespace(approval_id="pending")))
        agent.conversation_id = "conversation"
        agent.components = SimpleNamespace(coordinator=SimpleNamespace(handle=handle))
        agent._approval_decision = AsyncMock(return_value=None)
        await agent._turn("Before approving, can you explain the fee?")
        return agent.events.get_nowait()
    event = asyncio.run(run())
    assert event.content == "The operation still requires your confirmation."
    assert calls[0].approval_id is None
    assert calls[0].approval_decision is None
    assert calls[0].message == "Before approving, can you explain the fee?"
