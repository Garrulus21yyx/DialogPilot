import asyncio
import hashlib
import json

import pytest

from application.conversation_agent import ConversationAgent
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.response_assembly import _response_context
from infrastructure.conversation_tool_catalog import ConversationToolCatalog
from tests.test_approval_conversation import domain
from tests.test_conversation_agent import Provider, _state
from tests.test_response_assembly import _board


@pytest.mark.parametrize('description', [
    'Operation changes the entire object state; other operations may become ineligible.',
    '仅状态 ready 可执行；执行后变为 closed。',
    'Prefix. ' + 'Details. ' * 400 + ' Ending prerequisite.',
])
def test_business_description_survives_every_exposure_without_exposing_a_write(description):
    worker, context, _, _ = domain([])
    tool = next(t for t in worker._tool_manager.registered_tools if t.name == 'order_cancel')
    tool.description = description
    registry = worker._registry
    catalog = ConversationToolCatalog(worker._tool_manager)
    semantics = catalog.action_semantics(registry)
    assert semantics[0]['description'] == description
    assert semantics[0]['description_sha256'] == hashlib.sha256(description.encode()).hexdigest()
    wrapped = worker._action_tool(registry.actions[0].ref)
    assert 'business_operation_reference' in wrapped.description
    assert json.dumps('order_cancel') + ': ' + json.dumps(description, ensure_ascii=False) in worker._system(context)
    assert wrapped.name == 'prepare_order_cancel'
    assert 'order_cancel' not in {t['tool_id'] for t in catalog(registry, _state())}

    provider = Provider({'status': 'respond', 'response': 'Hello.'})
    agent = ConversationAgent(provider, tool_catalog=catalog)
    observation, state = TurnObservations('Hello'), _state()
    asyncio.run(agent.plan(observation, state, DeterministicResolver().resolve(observation, state), registry))
    assert 'business_action_semantics' not in provider.calls[0]
    evidence = _response_context(_board(), registry=registry, action_semantics=semantics)
    assert evidence['capability_policy']['business_actions'] == list(semantics)
    before = json.dumps(evidence, sort_keys=True)
    tool.description += ' Changed constraint.'
    assert json.dumps(evidence, sort_keys=True) == before
    assert catalog.action_semantics(registry)[0]['description_sha256'] != semantics[0]['description_sha256']


@pytest.mark.parametrize('mismatch', ['read_only', 'authority', 'principal'])
def test_unavailable_or_misregistered_actions_do_not_gain_semantic_authority(mismatch):
    worker, _, _, _ = domain([])
    tool = next(t for t in worker._tool_manager.registered_tools if t.name == 'order_cancel')
    if mismatch == 'read_only':
        tool.read_only = True
    elif mismatch == 'authority':
        tool.authority = 'wrong.source'
    else:
        tool.allowed_agents = ('unrelated',)
    with pytest.raises(ValueError):
        ConversationToolCatalog(worker._tool_manager).action_semantics(worker._registry)
