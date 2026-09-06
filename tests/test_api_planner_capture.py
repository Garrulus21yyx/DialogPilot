import asyncio
from types import SimpleNamespace

import pytest

from scripts.run_api_conversation_planner import CapturingClient


def test_call_limit_counts_failed_attempts_without_leaking_transport_details():
    calls = []

    class Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            raise ValueError('secret endpoint and credentials')

    client = CapturingClient(SimpleNamespace(messages=Messages()), limit=1)
    with pytest.raises(RuntimeError, match='calibration transport failed: ValueError'):
        asyncio.run(client.create(model='test', messages=[]))
    with pytest.raises(RuntimeError, match='call limit reached'):
        asyncio.run(client.create(model='test', messages=[]))
    assert len(calls) == 1
    assert len(client.captures) == 1
    assert client.captures[0]['error_type'] == 'ValueError'
    assert 'secret' not in str(client.captures)


def test_capture_preserves_request_response_usage_and_return_identity():
    usage = {'input_tokens': 100, 'output_tokens': 10, 'cache_read_input_tokens': 20}
    response = SimpleNamespace(
        usage=SimpleNamespace(model_dump=lambda **_: usage),
        model_dump=lambda **_: {'content': [{'type': 'text', 'text': '{}'}]},
    )

    class Messages:
        async def create(self, **kwargs):
            return response

    client = CapturingClient(SimpleNamespace(messages=Messages()), limit=1)
    request = {'model': 'test', 'messages': [], 'max_tokens': 800}
    assert asyncio.run(client.create(**request)) is response
    assert client.captures[0]['request'] == request
    assert client.captures[0]['usage'] == usage
    assert client.captures[0]['latency_ms'] >= 0
