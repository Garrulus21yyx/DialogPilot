import asyncio
from types import SimpleNamespace

import pytest

from core.llm_metrics import capture_llm_usage, create_message, record_external_llm_run
from core.model_policy import ModelProfile, ModelRole, ReasoningEffort
from core.provider_context_budget import ProviderContextBudgetExceeded


class FakeMessages:
    async def create(self, **_payload):
        return SimpleNamespace(
            id="msg_provider_123",
            content=[
                SimpleNamespace(type="thinking"),
                SimpleNamespace(type="text"),
            ],
            usage=SimpleNamespace(
                input_tokens=11,
                output_tokens=7,
                cache_creation_input_tokens=2,
                cache_read_input_tokens=3,
            ),
        )


def test_usage_collector_aggregates_official_tokens_and_marks_reasoning_unknown():
    profile = ModelProfile(
        "deepseek-v4-flash",
        ReasoningEffort.HIGH,
        "deepseek",
        512,
    )
    client = SimpleNamespace(messages=FakeMessages())

    with capture_llm_usage() as collector:
        asyncio.run(create_message(
            client,
            profile,
            ModelRole.INTENT,
            max_tokens=256,
            messages=[{"role": "user", "content": "test"}],
        ))

    summary = collector.summary()
    assert summary["total"]["calls"] == 1
    assert summary["total"]["input_tokens"] == 11
    assert summary["total"]["output_tokens"] == 7
    assert summary["total"]["thinking_calls"] == 1
    assert summary["total"]["reasoning_tokens"] is None
    assert summary["groups"][0]["role"] == "intent"
    assert summary["groups"][0]["reasoning"] == "high"
    assert summary["calls"][0]["provider_request_id"] == "msg_provider_123"
    assert summary["calls"][0]["estimated_input_tokens"] > 0
    assert summary["calls"][0]["max_context_tokens"] == 32768
    assert summary["calls"][0]["reserved_output_tokens"] == 512
    assert summary["total"]["input_estimation_ratio"] is not None
    assert 0 < summary["total"]["max_estimated_context_utilization"] < 1


def test_provider_boundary_counts_system_messages_tools_results_and_output_reserve():
    profile = ModelProfile("test-model", max_context_tokens=1024)
    request = {
        "max_tokens": 128,
        "system": "security policy " * 20,
        "messages": [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "call-1", "name": "lookup", "input": {},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "call-1",
                "content": "result " * 40,
            }]},
        ],
        "tools": [{
            "name": "lookup", "description": "lookup tool " * 20,
            "input_schema": {"type": "object", "properties": {}},
        }],
    }
    client = SimpleNamespace(messages=FakeMessages())
    asyncio.run(create_message(client, profile, ModelRole.REACT, **request))

    with capture_llm_usage() as collector:
        asyncio.run(create_message(client, profile, ModelRole.REACT, **request))
    call = collector.calls[0]
    assert call.estimated_input_tokens > 100
    assert call.estimated_input_tokens + request["max_tokens"] <= 1024


def test_provider_boundary_rejects_oversize_before_starting_external_side_effect():
    class CountingMessages:
        calls = 0

        async def create(self, **_payload):
            self.calls += 1
            return SimpleNamespace(content=[], usage=SimpleNamespace())

    messages = CountingMessages()
    client = SimpleNamespace(messages=messages)
    profile = ModelProfile("test-model", max_context_tokens=1024)
    with pytest.raises(ProviderContextBudgetExceeded) as exc_info:
        asyncio.run(create_message(
            client, profile, ModelRole.REACT,
            max_tokens=512,
            system="mandatory-system " * 2_000,
            messages=[{"role": "user", "content": "current request"}],
            tools=[{"name": "tool", "input_schema": {"type": "object"}}],
        ))
    assert messages.calls == 0
    assert exc_info.value.usage.output_reserve_tokens == 512
    assert exc_info.value.usage.system_tokens > 512


def test_calls_outside_capture_do_not_leak_into_later_run():
    profile = ModelProfile("deepseek-v4-flash", provider="deepseek")
    client = SimpleNamespace(messages=FakeMessages())

    asyncio.run(create_message(
        client,
        profile,
        ModelRole.WORKER,
        max_tokens=32,
        messages=[{"role": "user", "content": "outside"}],
    ))
    with capture_llm_usage() as collector:
        pass

    assert collector.summary()["total"]["calls"] == 0


def test_cancelled_provider_call_is_recorded_as_failed_attempt():
    class CancelledMessages:
        async def create(self, **_payload):
            raise asyncio.CancelledError()

    profile = ModelProfile("deepseek-v4-flash", provider="deepseek")
    client = SimpleNamespace(messages=CancelledMessages())

    with capture_llm_usage() as collector:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(create_message(
                client,
                profile,
                ModelRole.REACT,
                max_tokens=32,
                messages=[{"role": "user", "content": "timeout"}],
            ))

    assert collector.summary()["total"]["calls"] == 1
    assert collector.summary()["total"]["errors"] == 1


def test_external_structured_run_preserves_aggregate_usage_and_retry_count():
    profile = ModelProfile("deepseek-v4-pro", provider="deepseek")
    usage = SimpleNamespace(
        requests=2,
        input_tokens=11,
        output_tokens=7,
        cache_write_tokens=3,
        cache_read_tokens=5,
    )

    with capture_llm_usage() as collector:
        record_external_llm_run(
            profile,
            ModelRole.SYNTHESIS,
            usage,
            latency_ms=100.0,
            error="UnexpectedModelBehavior",
        )

    summary = collector.summary()
    assert summary["total"]["calls"] == 2
    assert summary["total"]["errors"] == 1
    assert summary["total"]["input_tokens"] == 11
    assert summary["total"]["output_tokens"] == 7
    assert summary["total"]["cache_creation_input_tokens"] == 3
    assert summary["total"]["cache_read_input_tokens"] == 5
    assert summary["total"]["latency_ms"]["sum"] == 100.0
