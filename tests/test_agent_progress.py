from langgraph.store.memory import InMemoryStore
"""Behavioral witnesses for native loop feedback and preserved completed work."""
import asyncio
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from application.agent_result import AgentResultStatus
from application.default_capability_registry import build_default_capability_registry
from infrastructure.target_framework_agent import TargetFrameworkAgent, _retryable_model_error
from tests.test_target_framework_agent import ScriptedToolModel, _context, _item, _manager


def read(call_id, query="same"):
    return AIMessage(content="", tool_calls=[{"name": "catalog_search", "id": call_id,
        "args": {"query": query}}])


@pytest.mark.parametrize("recover", [True, False])
def test_stagnation_gets_feedback_then_a_bounded_recovery_chance(recover):
    prompts = []
    class Model(ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
    calls = []
    model = Model(responses=[read("r1"), read("r2"), read("r3"),
        AIMessage(content="The catalog identifies PX-200.") if recover else read("r4")])
    agent = TargetFrameworkAgent(model, _manager(calls), result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")
    result = asyncio.run(agent(_context(replace(_item(), max_steps=10))))
    assert model.calls == 4
    assert len(calls) == (3 if recover else 4)
    assert "no new evidence" in str(prompts[-1])
    assert result.facts
    assert result.status is (AgentResultStatus.SUCCEEDED if recover else AgentResultStatus.BLOCKED)
    if not recover:
        assert result.reason_code == "AGENT_NO_PROGRESS"


def test_changing_evidence_is_progress_even_for_identical_calls():
    calls = []
    manager = _manager(calls)
    async def changing(params, context):
        calls.append(params)
        return {"canonical_model": f"P-{len(calls)}"}
    manager.registered_tools[0].handler = changing
    model = ScriptedToolModel(responses=[*[read(f"r{i}") for i in range(6)], AIMessage(content="Latest result recorded.")])
    result = asyncio.run(TargetFrameworkAgent(model, manager, result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(
            _context(replace(_item(), max_steps=10))))
    assert result.status is AgentResultStatus.SUCCEEDED
    assert len(calls) == 6


@pytest.mark.parametrize("error,retryable", [(ConnectionError("connection lost"), True), (ValueError("bad model setup"), False)])
def test_later_model_failure_retains_tool_evidence_and_actionable_continuation(error, retryable):
    class Model(ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.calls:
                raise error
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
    calls = []
    result = asyncio.run(TargetFrameworkAgent(Model(responses=[read("completed")]), _manager(calls), result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(_context()))
    assert result.retryable is retryable
    assert result.facts[0].source_ref == "completed"
    assert result.candidate_response is None
    assert len(calls) == 1
    assert "execution_feedback" in str(result.working_messages[-1])
    seen = []
    class Resumed(ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.extend(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
    context = replace(_context(), working_messages=result.working_messages, verified_facts=result.facts)
    completed = asyncio.run(TargetFrameworkAgent(Resumed(responses=[AIMessage(content="PX-200 is identified.")]),
        _manager(calls), result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(context))
    assert completed.status is AgentResultStatus.SUCCEEDED
    assert len(calls) == 1
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "completed" for m in seen)


@pytest.mark.parametrize("status,expected", [(400, False), (401, False), (408, True), (429, True), (500, True)])
def test_retry_classification_uses_status_not_exception_text(status, expected):
    error = RuntimeError("retry me")
    error.status_code = status
    assert _retryable_model_error(error) is expected
