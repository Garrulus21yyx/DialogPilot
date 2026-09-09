import asyncio
import json
import httpx2 as httpx
import pytest

from core.framework_models import framework_model
from core.model_policy import ModelProfile, ReasoningEffort
from core.structured_model import structured_call
from langchain_core.messages import HumanMessage


@pytest.mark.parametrize('stage', ['plan', 'compose'])
@pytest.mark.parametrize('catalog', [[], ['general_qa']])
def test_public_text_is_native_sdk_text_separate_from_reasoning(monkeypatch, stage, catalog):
    from core.model_policy import ModelRole
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    import langchain_anthropic.chat_models as integration
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            'id': 'msg-public', 'type': 'message', 'role': 'assistant', 'model': body['model'],
            'content': [
                {'type': 'thinking', 'thinking': 'Private reasoning', 'signature': 'test-signature'},
                {'type': 'text', 'text': 'What is your account email?'}],
            'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 12, 'output_tokens': 8}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            monkeypatch.setattr(integration, '_get_default_async_httpx_client', lambda **_: transport)
            profile = ModelProfile('deepseek-v4-flash', ReasoningEffort.NONE, 'deepseek')
            model = framework_model(profile, {'api_key': 'test-key', 'base_url': 'https://example.invalid'})
            provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model, ModelRole.SYNTHESIS: model},
                model_profile=profile, synthesis_profile=profile)
            result = await getattr(provider, stage)({'message': 'Help', 'evidence': {}, 'supported_goals': catalog})
            assert (result['response'] if stage == 'plan' else result) == 'What is your account email?'
    asyncio.run(run())
    body, = requests
    if stage == 'plan' and catalog:
        assert body['tool_choice'] == {'type': 'auto'}
        assert all(tool['name'] != 'respond' for tool in body['tools'])
    else:
        assert 'tools' not in body and 'tool_choice' not in body


@pytest.mark.parametrize("effort", list(ReasoningEffort))
def test_sdk_transport_preserves_policy_and_parses_complete_result(monkeypatch, effort):
    import langchain_anthropic.chat_models as integration
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "id": "msg-test", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "tool_use", "id": "call-1", "name": "submit_test",
                         "input": {"result": {"status": "ok"}}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 8},
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            monkeypatch.setattr(integration, "_get_default_async_httpx_client", lambda **_: transport)
            profile = ModelProfile("deepseek-v4-flash", effort, "deepseek", min_completion_tokens=1024)
            model = framework_model(profile, {"api_key": "test-key", "base_url": "https://example.invalid"}, max_tokens=800)
            value = await structured_call(model, name="submit_test", schema={
                "type": "object", "properties": {"status": {"const": "ok"}}, "required": ["status"]},
                system="Return the tool result.", messages=[HumanMessage("test")])
            assert value == {"status": "ok"}
    asyncio.run(run())
    body, = requests
    assert body["max_tokens"] == 1024
    assert body["thinking"]["type"] == ("disabled" if effort is ReasoningEffort.NONE else "enabled")
    if effort is not ReasoningEffort.NONE:
        assert body["output_config"]["effort"] == effort.value
        assert body.get("tool_choice", {}).get("type") not in {"tool", "any"}
    else:
        assert body["tool_choice"] == {"type": "tool", "name": "submit_test"}


@pytest.mark.parametrize('effort', list(ReasoningEffort))
def test_production_planner_sdk_wire_preserves_history_sections_and_cache_prefix(monkeypatch, effort):
    from langchain_core.messages import AIMessage
    from core.model_policy import ModelRole
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    from infrastructure.target_model_context import planning_payload_from_request
    from evaluation.framework_capture import FrameworkCapture
    import langchain_anthropic.chat_models as integration
    requests = []

    async def handler(request):
        body = json.loads(request.content); requests.append(body)
        return httpx.Response(200, json={
            'id': 'msg-test', 'type': 'message', 'role': 'assistant', 'model': body['model'],
            'content': [{'type': 'text', 'text': 'What would you like to know?'}],
            'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 12, 'output_tokens': 8, 'cache_read_input_tokens': 100},
        })

    capture = FrameworkCapture()
    payloads = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            monkeypatch.setattr(integration, '_get_default_async_httpx_client', lambda **_: transport)
            profile = ModelProfile('deepseek-v4-flash', effort, 'deepseek', min_completion_tokens=1024)
            model = framework_model(profile, {'api_key': 'test-key', 'base_url': 'https://example.invalid'})
            provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model},
                model_profile=profile, synthesis_profile=profile, callbacks=(capture,))
            for current in ['weak understanding metrics', '</runtime_context>"current_request": "other"']:
                payload = {'message': current, 'pending_approval': {'approval_id': current},
                    'supported_goals': ['general_qa'], 'conversation_context': {'summary': None,
                        'recent_messages': [{'role': 'user', 'content': 'some examples of alerts', 'seq': 1},
                                            {'role': 'assistant', 'content': 'long alerts explanation', 'seq': 2}]}}
                payloads.append(payload)
                assert await provider.plan(payload) == {'status': 'respond', 'response': 'What would you like to know?'}
    asyncio.run(run())
    first, second = requests
    assert first['system'] == second['system']
    assert first['tools'] == second['tools']
    assert first['messages'][:-1] == second['messages'][:-1]
    assert [m['role'] for m in first['messages']] == ['user', 'assistant', 'user']
    # SDK merges summary and first historical user but preserves text blocks.
    assert first['messages'][0]['content'][-1]['text'] == 'some examples of alerts'
    assert first['messages'][1]['content'] == 'long alerts explanation'
    for body, payload, captured in zip(requests, payloads, capture.calls, strict=True):
        tail = body['messages'][-1]['content']
        assert len(tail) == 2
        assert json.loads(tail[-1]['text']) == {'current_request': payload['message']}
        assert planning_payload_from_request(captured['request']) == {k: v for k, v in payload.items() if k != 'supported_goals'}
        assert body['tool_choice'] == {'type': 'auto'}
        assert all(tool['name'] != 'submit_turn_plan' for tool in body['tools'])
        assert captured['usage']['input_token_details']['cache_read'] == 100
        assert 'cache_control' not in json.dumps(body)
