"""M4-T03P final provider-call budget invariant proofs."""
import pytest

from core.model_policy import ModelProfile, ModelRole
from core.provider_context_budget import (
    ProviderContextBudget,
    ProviderContextBudgetExceeded,
)


@pytest.mark.parametrize("tool_count", [0, 1, 10, 100])
@pytest.mark.parametrize("result_chars", [0, 100, 10_000])
def test_arbitrary_tool_schema_and_result_sizes_are_admitted_or_typed_rejected(
    tool_count, result_chars,
):
    profile = ModelProfile("test-model", max_context_tokens=2048)
    tools = [
        {
            "name": f"tool_{index}",
            "description": "bounded schema",
            "input_schema": {"type": "object", "properties": {}},
        }
        for index in range(tool_count)
    ]
    request = {
        "max_tokens": 256,
        "system": "mandatory security and task contract",
        "tools": tools,
        "messages": [{"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "call-1",
            "content": "x" * result_chars,
        }]}],
    }
    try:
        usage = ProviderContextBudget().validate(profile, ModelRole.REACT, request)
    except ProviderContextBudgetExceeded as exc:
        assert exc.usage.total_reserved_tokens > 2048
    else:
        assert usage.total_reserved_tokens <= 2048
        assert usage.tool_schema_tokens >= tool_count * 4


@pytest.mark.parametrize('consumer', ['generator', 'reranker'])
def test_structured_pydantic_requests_enforce_actual_sdk_budget_before_network(consumer):
    import asyncio
    import httpx2 as httpx
    from anthropic import AsyncAnthropic
    from mcp.grounded_answer_generator import GroundedAnswerGenerator
    from mcp.result_reranker import ResultReranker, RerankCandidate
    from mcp.context_packer import ContextCandidate
    calls = []
    def transport(request):
        calls.append(request)
        raise AssertionError('over-budget request reached HTTP')
    async def run():
        client = AsyncAnthropic(api_key='fake-test-key', http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
        profile = ModelProfile('claude-sonnet-4-20250514', max_context_tokens=1024)
        try:
            if consumer == 'generator':
                return await GroundedAnswerGenerator(client, profile).generate('policy?', (ContextCandidate('c','s','x'*12000,0,12000),))
            return await ResultReranker(client, profile).rerank('policy?', (RerankCandidate('c','x'*12000),))
        finally:
            await client.close()
    result = asyncio.run(run())
    assert result.error and 'ProviderContextBudgetExceeded' in result.error
    assert not calls
