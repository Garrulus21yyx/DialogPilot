"""Facts stay intact unless the complete pinned task cannot be admitted."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.store.memory import InMemoryStore
from application.agent_result import FactRecord, FactSourceKind
from application.context_budget import ModelContextBudgetExceeded
from application.default_capability_registry import build_default_capability_registry
from infrastructure.target_framework_agent import TargetFrameworkAgent
from tests.test_target_framework_agent import _context, _manager, ScriptedToolModel


def make_agent():
    model = ScriptedToolModel(responses=[])
    return TargetFrameworkAgent(model, _manager([]), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry('tenant-a'), system_prompt='Inspect only.')


@pytest.mark.parametrize('size,overhead,expected_pointer', [(14000, 1000, False), (14000, 11500, True), (80000, 1000, True)])
def test_fact_admission_counts_full_prompt_and_overhead(size, overhead, expected_pointer):
    async def run():
        agent = make_agent()
        raw = json.dumps({'condition': 'Only under the stated conditions.', 'body': 'x'*size}, sort_keys=True, separators=(',', ':'))
        fact = FactRecord('source', 'knowledge.active_source', raw, list(FactSourceKind)[0],
            'reference', 'fixture', 'v1', datetime.now(timezone.utc))
        context = replace(_context(), verified_facts=(fact,))
        prompt = await agent._prepare_prompt(context, overhead_tokens=overhead)
        assert count_tokens_approximately([HumanMessage(content=prompt)]) + overhead <= 14200
        value = json.loads(prompt)['verified_facts'][0]['value']
        if expected_pointer:
            assert value['complete'] is False
            stored = await agent._archive.load(context, value['result_ref'])
            assert stored['content'] == raw
        else:
            assert value == json.loads(raw)
            assert await agent._archive.store.asearch(agent._archive.namespace(context)) == []
        assert context.verified_facts[0].value_json == raw
        assert agent._model.calls == 0
    asyncio.run(run())


def test_irreducible_task_budget_error_is_typed_without_model_call():
    async def run():
        agent = make_agent()
        with pytest.raises(ModelContextBudgetExceeded):
            await agent._prepare_prompt(_context(), overhead_tokens=14200)
        assert agent._model.calls == 0
    asyncio.run(run())
