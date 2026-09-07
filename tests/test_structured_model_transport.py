import asyncio
import json
import httpx2 as httpx
import pytest

from core.framework_models import framework_model
from core.model_policy import ModelProfile, ReasoningEffort
from core.structured_model import structured_call


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
                system="Return the tool result.", content="test")
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
