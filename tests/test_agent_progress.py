from langgraph.store.memory import InMemoryStore
"""Behavioral witnesses for native loop feedback and preserved completed work."""
import asyncio
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from application.agent_result import AgentResultStatus
from application.default_capability_registry import build_default_capability_registry
from infrastructure.target_framework_agent import TargetFrameworkAgent
from core.framework_models import retryable_model_error
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
    agent = TargetFrameworkAgent(model, _manager(calls), review_model=model, review_available_tokens=14200, result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")
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
    result = asyncio.run(TargetFrameworkAgent(model, manager, review_model=model, review_available_tokens=14200, result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(
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
    result = asyncio.run(TargetFrameworkAgent(Model(responses=[read("completed")]), _manager(calls), review_model=Model(responses=[read("completed")]), review_available_tokens=14200, result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(_context()))
    assert result.retryable is retryable
    assert result.facts[0].source_ref == "completed"
    assert result.candidate_response is None
    assert len(calls) == 1
    assert "execution_feedback" in str(result.working_messages[-1])
    compacted = replace(result, working_messages=())
    assert compacted.execution_feedback == result.execution_feedback
    assert any(row.get("call_id") == "completed" for row in compacted.execution_feedback)
    seen = []
    class Resumed(ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.extend(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
    context = replace(_context(), working_messages=result.working_messages, verified_facts=result.facts)
    completed = asyncio.run(TargetFrameworkAgent(Resumed(responses=[AIMessage(content="PX-200 is identified.")]),
        _manager(calls), review_model=Resumed(responses=[AIMessage(content="PX-200 is identified.")]), review_available_tokens=14200, result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(context))
    assert completed.status is AgentResultStatus.SUCCEEDED
    assert len(calls) == 1
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "completed" for m in seen)


@pytest.mark.parametrize("status,expected", [(400, False), (401, False), (408, True), (429, True), (500, True)])
def test_retry_classification_uses_status_not_exception_text(status, expected):
    error = RuntimeError("retry me")
    error.status_code = status
    assert retryable_model_error(error) is expected


@pytest.mark.parametrize("feedback", [(), ({"stage": "tool", "status": "denied"},),
    ({"stage": "tool", "status": "succeeded"}, {"stage": "domain_model", "retryable": True})])
def test_execution_diagnostics_roundtrip_without_working_messages(feedback):
    from application.agent_result import AgentResult
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    result = AgentResult("work", "general", AgentResultStatus.BLOCKED, "STOPPED", "test",
                         execution_feedback=feedback)
    serializer = target_checkpoint_serializer()
    restored = serializer.loads_typed(serializer.dumps_typed(result))
    assert restored == result
    assert list(restored.execution_feedback) == list(feedback)


@pytest.mark.parametrize('persisted', [False, True])
def test_rephrased_reordered_knowledge_has_same_progress_in_archive_and_inline(persisted):
    from copy import deepcopy
    from types import SimpleNamespace
    from infrastructure.target_agent_middleware import AgentProgressMiddleware
    from infrastructure.target_context_compaction import ToolResultPersistence
    from infrastructure.target_result_archive import TargetResultArchive
    from tests.test_knowledge_tool_contract import evidence_result
    async def run():
        state = {}
        progress = AgentProgressMiddleware()
        archive = TargetResultArchive(InMemoryStore())
        persistence = ToolResultPersistence(archive)
        data = evidence_result()
        second = deepcopy(data['evidence_pack']['items'][0])
        second['chunk_id'] = 'second'
        data['evidence_pack']['items'].append(second)
        for turn in range(4):
            current = deepcopy(data)
            current['evidence_pack']['query'] = f'rephrase {turn}'
            current['evidence_pack']['items'].reverse() if turn % 2 else None
            if turn == 2:
                current['evidence_pack']['items'] = current['evidence_pack']['items'][:1]
            current['diagnostics'] = {'duration': turn}
            artifact = {'schema': 'tool-result-v1', 'result': {'authority': 'knowledge.active_source', 'status': 'SUCCESS', 'data': current}}
            message = ToolMessage(content='evidence', name='knowledge_search', tool_call_id=str(turn), artifact=artifact)
            request = AIMessage(content='', tool_calls=[{'id': str(turn), 'name': 'knowledge_search',
                'args': {'query': f'rephrase {turn}'}}])
            inline = await progress.abefore_model({**state, 'messages': [request, message]}, None)
            if persisted:
                async def handler(request): return message
                update = await persistence.awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=_context())), handler)
                message = update.update['messages'][0]
            update = await progress.abefore_model({**state, 'messages': [request, message]}, None)
            assert {k: v for k, v in update.items() if k != 'messages'} == {k: v for k, v in inline.items() if k != 'messages'}
            state.update(update)
        assert state['progress_blocked'] and state['jump_to'] == 'end'
    asyncio.run(run())


def test_knowledge_new_source_revision_and_general_business_change_remain_progress():
    from copy import deepcopy
    from infrastructure.target_agent_middleware import tool_observation_digest
    from tests.test_knowledge_tool_contract import evidence_result
    original = {'authority': 'knowledge.active_source', 'data': evidence_result()}
    for mutate in (lambda x: x['data']['evidence_pack']['items'][0]['source_ref'].update(source_revision='v2'),
                   lambda x: x['data']['evidence_pack'].update(index_manifest_fingerprint='c' * 64)):
        updated = deepcopy(original)
        mutate(updated)
        assert tool_observation_digest(original) != tool_observation_digest(updated)
    assert tool_observation_digest({'data': {'status': 'paid'}}) != tool_observation_digest({'data': {'status': 'refunded'}})
    assert tool_observation_digest({'authority': 'knowledge.active_source', 'data': {'status': 'NO_EVIDENCE', 'query': 'a'}}) != tool_observation_digest({'authority': 'knowledge.active_source', 'data': {'status': 'NO_EVIDENCE', 'query': 'b'}})
