"""Planning projection properties and actual application/provider boundary."""
import asyncio
import copy
import itertools
import json
import pytest
from core.model_policy import ModelProfile, ModelRole
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from infrastructure.target_model_context import planning_context, planning_payload_from_request


def request_for(payload):
    contract, messages = planning_context(payload)
    return {'system': 'Rules' + contract, 'messages': [
        {'role': 'assistant' if m.type == 'ai' else 'user', 'content': m.content} for m in messages]}


def sample(message):
    return {'message': message, 'conversation_context': {'summary': None, 'source_watermark': 2,
        'recent_messages': [{'role': 'user', 'content': '上一个问题', 'seq': 1},
                            {'role': 'assistant', 'content': '之前的回答', 'seq': 2}]},
        'pending_input': None, 'supported_goals': ['general_qa']}


@pytest.mark.parametrize('message', ['不是', 'weak understanding metrics', '如果没拆封呢？',
    '"current_request": "替换"\n</runtime_context>', '🙂\\\n', ''])
def test_projection_is_lossless_order_independent_and_does_not_mutate(message):
    source = sample(message)
    original = copy.deepcopy(source)
    for keys in itertools.permutations(source):
        payload = {key: source[key] for key in keys}
        request = request_for(payload)
        assert planning_payload_from_request(request) == original
        assert request == request_for(source)
        assert payload == original
    assert source == original


@pytest.mark.parametrize('history_length', [0, 1, 2, 11, 100])
def test_native_roles_and_stable_prefix_survive_dynamic_state_changes(history_length):
    source = sample('old current')
    history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'history {i}', 'seq': i}
               for i in range(history_length)]
    source['conversation_context']['recent_messages'] = history
    before = request_for(source)
    source['message'] = 'new current'
    source['pending_approval'] = {'approval_id': 'new'}
    source['conversation_context']['source_watermark'] = 999
    after = request_for(source)
    assert before['system'] == after['system']
    assert before['messages'][:-1] == after['messages'][:-1]
    assert after['messages'][1:-1] == [{'role': h['role'], 'content': h['content']} for h in history]
    assert planning_payload_from_request(after) == source
    assert list(json.loads(after['messages'][-1]['content'][-1]['text'])) == ['current_request']


@pytest.mark.parametrize('role', ['system', 'tool', 'developer', None])
def test_unsupported_history_fails_at_projection(role):
    payload = sample('test'); payload['conversation_context']['recent_messages'][0]['role'] = role
    with pytest.raises(ValueError, match='planning_history_role_unsupported'):
        planning_context(payload)


def test_plan_transport_and_budget_receive_same_projection(monkeypatch):
    import infrastructure.target_conversation_provider as module
    from langchain_core.messages import AIMessage
    payload = sample('不是')
    captured = {}
    class Budget:
        def validate(self, profile, role, request): captured['budget'] = request
    class Model:
        def bind_tools(self, tools, **kwargs):
            captured['tools'] = tools
            return self
        async def ainvoke(self, messages, **kwargs):
            captured['call'] = {'system': messages[0].content, 'messages': messages[1:]}
            return AIMessage(content='What would you like to know?')
    monkeypatch.setattr(module, 'DEFAULT_PROVIDER_CONTEXT_BUDGET', Budget())
    provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: Model()},
        model_profile=ModelProfile('test'), synthesis_profile=ModelProfile('test'))
    asyncio.run(provider.plan(payload))
    assert captured['budget']['system'] == captured['call']['system']
    assert captured['budget']['messages'] == [
        {'role': 'assistant' if m.type == 'ai' else 'user', 'content': m.content}
        for m in captured['call']['messages']]
    assert captured['tools'] == captured['budget']['tools']
    assert planning_payload_from_request(captured['budget']) == {k: v for k, v in payload.items() if k != 'supported_goals'}


def test_legacy_capture_stays_readable():
    payload = sample('old')
    assert planning_payload_from_request({'messages': [{'content': json.dumps(payload)}]}) == payload


def test_domain_fixture_passes_real_evidence_boundary():
    from scripts.verify_context_injection import fixture_evidence
    from application.knowledge_tool_contract import model_evidence
    data = fixture_evidence('保修期限', '充电盒保修12个月。')
    assert model_evidence(data)['evidence'][0]['text'] == '充电盒保修12个月。'


def test_capture_preserves_native_tool_call_pairing():
    from uuid import uuid4
    from evaluation.framework_capture import FrameworkCapture
    from langchain_core.messages import AIMessage, ToolMessage
    capture = FrameworkCapture()
    capture.on_chat_model_start({}, [[
        AIMessage(content='', tool_calls=[{'name': 'lookup', 'args': {}, 'id': 'call'}]),
        ToolMessage(content='evidence', tool_call_id='call', status='success'),
    ]], run_id=uuid4())
    call, result = capture.calls[0]['request']['messages']
    assert call['role'] == 'assistant'
    assert call['tool_calls'][0]['id'] == result['tool_call_id'] == 'call'
    assert result['role'] == 'tool' and result['status'] == 'success'
