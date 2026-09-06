import asyncio
from application.conversation_agent import ConversationAgent
from tests.test_conversation_agent import Provider, _invoke
from infrastructure.target_conversation_provider import (
    AnthropicConversationPlanningProvider,
)


def test_missing_field_schema_is_provided_by_validation_owner():
    provider = Provider({"status": "out_of_scope"})
    _invoke(ConversationAgent(provider), "想咨询售后政策")
    assert set(provider.calls[0]["missing_fields_schema"]) == {
        "order_id",
        "asset_id",
        "new_address",
        "customer_service_goal",
    }


import pytest
from types import SimpleNamespace
from core.model_policy import ModelProfile, ReasoningEffort


@pytest.mark.parametrize("method", ["plan", "compose"])
@pytest.mark.parametrize("effort", list(ReasoningEffort))
def test_conversation_transport_preserves_role_reasoning_and_budget(method, effort):
    profile = ModelProfile(
        "deepseek-v4-flash", effort, "deepseek",
        min_completion_tokens=1024 if effort is not ReasoningEffort.NONE else 0,
    )
    calls = []

    class Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return _transport_response(kwargs)

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=profile, synthesis_profile=profile, max_tokens=800,
    )
    asyncio.run(getattr(provider, method)({"message": "policy question", "allowed_claims":[{"claim_id":"outcome:1","kind":"WORK_ITEM_OUTCOME"}]}))
    request = calls[0]
    assert request["model"] == profile.model
    if method == "compose":
        assert request["tool_choice"]["type"] == ("tool" if effort is ReasoningEffort.NONE else "auto")
    assert request["max_tokens"] == max(800, profile.min_completion_tokens)
    if effort is ReasoningEffort.NONE:
        assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    else:
        assert request["extra_body"] == {
            "thinking": {"type": "enabled"}, "output_config": {"effort": effort.value},
        }


@pytest.mark.parametrize("method", ["plan", "compose"])
def test_full_conversation_request_rejects_overflow_before_transport(method):
    from core.provider_context_budget import ProviderContextBudgetExceeded
    profile = ModelProfile("deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 8192, 16000)

    class Messages:
        async def create(self, **kwargs):
            pytest.fail("over-budget input must never reach transport")

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=profile, synthesis_profile=profile,
    )
    with pytest.raises(ProviderContextBudgetExceeded):
        asyncio.run(getattr(provider, method)({"message": "政策" * 12000, "allowed_claims":[{"claim_id":"outcome:1","kind":"WORK_ITEM_OUTCOME"}]}))


def test_agent_maps_final_provider_budget_failure_to_context_outcome():
    profile = ModelProfile("deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 1500, 1600)

    class Messages:
        async def create(self, **kwargs):
            pytest.fail("full prompt cannot fit and must not call API")

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=profile, synthesis_profile=profile,
    )
    proposal, _, _ = _invoke(ConversationAgent(provider), "想咨询售后政策")
    assert proposal.reason_code == "CONTEXT_BUDGET_EXCEEDED"


def test_planning_and_composition_use_their_own_model_and_budget():
    calls = []
    class Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return _transport_response(kwargs)
    intent = ModelProfile('deepseek-v4-flash', ReasoningEffort.LOW, 'deepseek', min_completion_tokens=2048)
    synthesis = ModelProfile('deepseek-v4-pro', ReasoningEffort.NONE, 'deepseek')
    provider = AnthropicConversationPlanningProvider(SimpleNamespace(messages=Messages()),
        model_profile=intent, synthesis_profile=synthesis)
    asyncio.run(provider.plan({'message':'查订单及政策'}))
    asyncio.run(provider.compose({'allowed_claims':[{'claim_id':'outcome:1','kind':'WORK_ITEM_OUTCOME'}]}))
    assert [c['model'] for c in calls] == [intent.model, synthesis.model]
    assert [c['max_tokens'] for c in calls] == [2048, 800]
    assert calls[0]['extra_body']['thinking']['type'] == 'enabled'
    assert calls[1]['extra_body']['thinking']['type'] == 'disabled'


def test_composition_fits_its_own_input_budget():
    from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
    class Provider:
        version = 'test'
        async def compose(self, payload):
            return payload
    agent = ConversationAgent(Provider(),
        context_budget=ContextBudgetManager(context_window_tokens=16000),
        synthesis_context_budget=ContextBudgetManager(context_window_tokens=2000,
            reserved_output_tokens=800, protocol_reserve_tokens=600))
    with pytest.raises(ModelContextBudgetExceeded):
        asyncio.run(agent.compose({'allowed_claims': [{'text':'政策条件' * 2000}]}))


def _transport_response(request):
    if request.get('tools', [{}])[0].get('name') == 'submit_turn_plan':
        return SimpleNamespace(stop_reason='tool_use', content=[SimpleNamespace(
            type='tool_use', name='submit_turn_plan', input={'status': 'out_of_scope'})])
    if 'tools' in request:
        import json
        sid = json.loads(request['messages'][0]['content'])['support_catalog'][0]['support_id']
        return SimpleNamespace(stop_reason='tool_use', content=[SimpleNamespace(
            type='tool_use', name='submit_composed_response',
            input={'segments':[{'text':'已查询。', 'support_ids':[sid]}]})])
    return SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text='{}')])


@pytest.mark.parametrize('value', [None, {}, {'response':'ok','used_claim_ids':'id'},
    {'response':'','used_claim_ids':['id']}, {'response':'ok','used_claim_ids':[]},
    {'response':'ok','used_claim_ids':['id','id']}, {'response':'ok','used_claim_ids':[{}]},
    {'response':'ok','used_claim_ids':['id'],'extra':1}])
def test_composition_rejects_invalid_structured_values(value):
    from application.conversation_agent import ConversationProviderOutputError
    class Messages:
        async def create(self, **request):
            return SimpleNamespace(stop_reason='tool_use',content=[SimpleNamespace(
                type='tool_use',name='submit_composed_response',input=value)])
    profile=ModelProfile('test')
    provider=AnthropicConversationPlanningProvider(SimpleNamespace(messages=Messages()),
        model_profile=profile,synthesis_profile=profile)
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(provider.compose({'allowed_claims':[{'claim_id':'id','kind':'FACT'}]}))


@pytest.mark.parametrize('stop,name,count', [('max_tokens','submit_composed_response',1),
    ('end_turn','submit_composed_response',1), ('tool_use','other_tool',1),
    ('tool_use','submit_composed_response',0), ('tool_use','submit_composed_response',2)])
def test_composition_requires_single_complete_expected_tool(stop,name,count):
    from application.conversation_agent import ConversationProviderOutputError
    class Messages:
        async def create(self, **request):
            block=SimpleNamespace(type='tool_use',name=name,input={'response':'ok','used_claim_ids':['id']})
            return SimpleNamespace(stop_reason=stop,content=[block]*count)
    profile=ModelProfile('test')
    provider=AnthropicConversationPlanningProvider(SimpleNamespace(messages=Messages()),
        model_profile=profile,synthesis_profile=profile)
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(provider.compose({'allowed_claims':[{'claim_id':'id','kind':'FACT'}]}))


def test_goal_meanings_are_supplied_by_the_compiler_owner():
    from application.conversation_agent import planning_goal_descriptions
    provider=Provider({'status':'out_of_scope'})
    _invoke(ConversationAgent(provider),'先查订单，再解释改址规则')
    payload=provider.calls[0]
    assert set(payload['goal_descriptions'])==set(payload['supported_goals'])
    assert all(description.strip() for description in payload['goal_descriptions'].values())
    # A consumer's rendering changes cannot mutate the supported planner vocabulary.
    copy=planning_goal_descriptions();copy['invented_action']='Perform a new action'
    assert 'invented_action' not in planning_goal_descriptions()


@pytest.mark.parametrize('value', [
    {'status': 'out_of_scope'},
    {'status': 'insufficient_context', 'missing_fields': ['order_id']},
    {'status': 'resolved', 'goals': [{'kind': 'order_status', 'order_id': 'DP9303',
                                    'order_id_source_ref': 'turn-message:current:reference:1'}]},
])
def test_native_planner_preserves_all_supported_wire_states(value):
    from application.conversation_agent import planning_output_schema
    class Messages:
        async def create(self, **request):
            assert request['tools'][0]['input_schema'] == planning_output_schema()
            return SimpleNamespace(stop_reason='tool_use', content=[SimpleNamespace(
                type='tool_use', name='submit_turn_plan', input=value)])
    profile = ModelProfile('test')
    provider = AnthropicConversationPlanningProvider(SimpleNamespace(messages=Messages()),
        model_profile=profile, synthesis_profile=profile)
    assert asyncio.run(provider.plan({'message': 'test'})) == value


@pytest.mark.parametrize('value', [
    None, {}, {'status': 'unknown'}, {'status': 'resolved', 'goals': []},
    {'status': 'out_of_scope', 'goals': [{'kind': 'order_status'}]},
    {'status': 'insufficient_context', 'missing_fields': []},
    {'status': 'resolved', 'goals': [{'kind': 'order_status', 'goal_id': 1}]},
    {'status': 'resolved', 'goals': [{'kind': 'order_status',
                                    'order_id': {'value': 'DP9303', 'source_ref': 'ref'}}]},
    {'status': 'resolved', 'goals': [{'kind': 'order_status', 'depends_on': ['a', 'a']}]},
])
def test_native_planner_rejects_invalid_wire_states(value):
    from application.conversation_agent import ConversationProviderOutputError
    class Messages:
        async def create(self, **request):
            return SimpleNamespace(stop_reason='tool_use', content=[SimpleNamespace(
                type='tool_use', name='submit_turn_plan', input=value)])
    profile = ModelProfile('test')
    provider = AnthropicConversationPlanningProvider(SimpleNamespace(messages=Messages()),
        model_profile=profile, synthesis_profile=profile)
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(provider.plan({'message': 'test'}))


@pytest.mark.parametrize('stop,name,count', [
    ('max_tokens', 'submit_turn_plan', 1), ('end_turn', 'submit_turn_plan', 1),
    ('tool_use', 'other_tool', 1), ('tool_use', 'submit_turn_plan', 0),
    ('tool_use', 'submit_turn_plan', 2),
])
def test_native_planner_requires_complete_single_output(stop, name, count):
    from application.conversation_agent import ConversationProviderOutputError
    class Messages:
        async def create(self, **request):
            return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(
                type='tool_use', name=name, input={'status': 'out_of_scope'})] * count)
    profile = ModelProfile('test')
    provider = AnthropicConversationPlanningProvider(SimpleNamespace(messages=Messages()),
        model_profile=profile, synthesis_profile=profile)
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(provider.plan({'message': 'test'}))
