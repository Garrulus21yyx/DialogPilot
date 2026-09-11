import asyncio
import json
from dataclasses import replace

import pytest

from application.business_observation import BusinessObservation, capture_business_observations
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import AgentContextView
from application.work_item import ArgumentValue, ControlMode
from infrastructure.conversation_observation_tool import (
    AUTHORITY, TOOL_ID, install_observation_capability, observation_document,
)
from infrastructure.conversation_tool_catalog import ConversationToolCatalog
from infrastructure.target_tool_execution import TargetToolExecutor
from mcp.tool_manager import MCPToolManager
from tests.test_business_observation_continuity import observed_board
from tests.test_conversation_agent import _state
from tests.test_postgres_publication import publication_components as _publication_components, _final  # noqa: F401


def setup_reader():
    original = BusinessObservation.model_validate(capture_business_observations(observed_board())[0])
    class Reader:
        calls = []
        def read_business(self, invocation, **kwargs):
            self.calls.append((invocation, kwargs))
            return original
    reader = Reader()
    manager = MCPToolManager('test', model='test')
    base = build_default_capability_registry('tenant-a')
    base = replace(base, agents=tuple(replace(agent, allowed_tool_ids=(), allowed_skill_ids=()) for agent in base.agents),
                   skills=(), actions=(), flows=())
    registry = install_observation_capability(base, manager, reader)
    return original, reader, manager, registry


def test_native_catalog_and_executor_use_the_registered_read_with_runtime_identity():
    original, reader, manager, registry = setup_reader()
    reads = ConversationToolCatalog(manager)(registry, _state())
    assert {read['tool_id'] for read in reads} == {TOOL_ID}
    base = observed_board().work_items[0]
    params = {'publication_id': 'p1', 'observation_id': original.observation_id,
              'pointer': '/facts/0/value/status'}
    item = replace(base, control_mode=ControlMode.DIRECT, control=None, allowed_tools=(TOOL_ID,),
        requirement_ids=(AUTHORITY,), arguments=tuple(ArgumentValue.create(k,v) for k,v in params.items()),
        registry_fingerprint=registry.fingerprint)
    identity = {'tenant_id':'tenant-a','user_id':'user-a','conversation_id':'conversation-a'}
    result = asyncio.run(TargetToolExecutor(manager, registry=registry)(
        AgentContextView(item, 'Explain the previous result', (), (), (), 2000, identity)))
    assert result.status.value == 'SUCCEEDED'
    data = json.loads(result.facts[0].value_json)
    assert json.loads(data['text']) == 'shipped' and data['complete']
    assert vars(reader.calls[0][0]) == identity
    assert not result.action_receipts
    assert capture_business_observations(replace(observed_board(), work_items=(item,), results=(result,))) == ()


@pytest.mark.parametrize('change', [{'limit':0}, {'limit':2001}, {'offset':-1},
    {'tenant_id':'other'}, {'observation_id':'not-a-reference'}])
def test_manager_rejects_bad_parameters_before_reading_original(change):
    original, reader, manager, registry = setup_reader()
    result = asyncio.run(manager.execute_for_agent(TOOL_ID,
        {'publication_id':'p1','observation_id':original.observation_id,**change},
        agent_type=registry.agents[0].execution_principal,
        context={'tenant_id':'tenant-a','user_id':'user-a','conversation_id':'conversation-a'},
        allowed_tool_ids=(TOOL_ID,)))
    assert not result.success and reader.calls == []


@pytest.mark.parametrize('numeric', [int, float])
def test_bounded_pages_reconstruct_exact_json_without_new_business_lookup(numeric):
    original, reader, manager, registry = setup_reader()
    offset, pages = 0, []
    while True:
        result = asyncio.run(manager.execute_for_agent(TOOL_ID,
            {'publication_id':'p1','observation_id':original.observation_id,'offset':numeric(offset),'limit':numeric(100)},
            agent_type=registry.agents[0].execution_principal,
            context={'tenant_id':'tenant-a','user_id':'user-a','conversation_id':'conversation-a'},
            allowed_tool_ids=(TOOL_ID,)))
        assert result.success
        data = result.data
        assert len(data['text']) <= 100 and data['status'] == 'HISTORICAL'
        pages.append(data['text'])
        if data['next_offset'] is None:
            break
        assert data['next_offset'] > offset
        offset = data['next_offset']
    assert json.loads(''.join(pages)) == observation_document(original)


def test_historical_read_retains_only_selected_fact_source_as_private_provenance():
    original, _, manager, registry = setup_reader()
    result = asyncio.run(manager.execute_for_agent(TOOL_ID, {
        'publication_id': 'p1', 'observation_id': original.observation_id,
        'pointer': '/facts/0/value/status',
    }, agent_type=registry.agents[0].execution_principal,
        context={'tenant_id': 'tenant-a', 'user_id': 'user-a', 'conversation_id': 'conversation-a'},
        allowed_tool_ids=(TOOL_ID,), call_id='observation-read-1'))
    assert result.causal_source_call_ids == (original.facts[0].source_ref,)
    assert 'causal_source_call_ids' not in result.output_for_model


def test_custom_registry_keeps_business_actions_and_principals():
    original, reader, manager, default = setup_reader()
    base = build_default_capability_registry('retail')
    agent = replace(base.agents[0], tool_principal='external-retail-worker')
    # An external bundle's principal is not inferred from a hardcoded domain map.
    custom = replace(base, agents=(agent,), skills=(), flows=(), actions=())
    adapted = install_observation_capability(custom, manager, reader)
    assert adapted.agents[0].execution_principal == 'external-retail-worker'
    assert adapted.tools[:-1] == custom.tools
    assert custom.agents[0].allowed_tool_ids == agent.allowed_tool_ids
    assert TOOL_ID in adapted.agents[0].allowed_tool_ids
    assert adapted.fingerprint != custom.fingerprint
    restored = install_observation_capability(adapted, manager, reader)
    assert restored.fingerprint == adapted.fingerprint
    conflicting = replace(adapted, tools=tuple(replace(tool, authority='other') if tool.tool_id == TOOL_ID else tool
                                              for tool in adapted.tools))
    with pytest.raises(ValueError, match='conflicts'):
        install_observation_capability(conflicting, manager, reader)


def test_real_original_is_read_through_tool_manager_without_cross_scope_access(_publication_components):  # noqa: F811
    from infrastructure.postgres_conversation_evidence import PostgresConversationEvidence
    pool, identity, service, _ = _publication_components
    original = BusinessObservation.model_validate(capture_business_observations(observed_board())[0])
    selected = service.select_final_response(replace(_final(identity),
        business_observations=(original.model_dump(mode='json'),)))
    manager = MCPToolManager('test', model='test')
    registry = install_observation_capability(build_default_capability_registry(str(identity.tenant_id)),
                                             manager, PostgresConversationEvidence(pool))
    params = {'publication_id':selected.record.publication_id,'observation_id':original.observation_id,
              'pointer':'/facts/0/value/status'}
    scope = {key:str(getattr(identity,key)) for key in ('tenant_id','user_id','conversation_id')}
    for change in ({}, {'tenant_id':'other'}, {'user_id':'other'}, {'conversation_id':'other'}):
        result = asyncio.run(manager.execute_for_agent(TOOL_ID, params,
            agent_type=registry.agents[0].execution_principal, context={**scope,**change},
            allowed_tool_ids=(TOOL_ID,)))
        assert result.success is (not change)
        if not change:
            assert json.loads(result.data['text']) == 'shipped'
        else:
            assert result.status == 'rejected' and result.data is None


@pytest.mark.parametrize('change', [{'authority':'other'}, {'read_only':False},
    {'schema':{'type':'object'}}, {'requires_approval':True}])
def test_install_does_not_replace_conflicting_execution_contract(change):
    _, reader, manager, registry = setup_reader()
    existing = next(tool for tool in manager.registered_tools if tool.name == TOOL_ID)
    conflicting = replace(existing, **change)
    manager.register(conflicting)
    with pytest.raises(ValueError, match='registered execution contract'):
        install_observation_capability(registry, manager, reader)
    assert next(tool for tool in manager.registered_tools if tool.name == TOOL_ID) is conflicting
