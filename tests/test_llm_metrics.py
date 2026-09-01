import asyncio
from types import SimpleNamespace

import pytest

from core.llm_metrics import capture_llm_usage, create_message, record_external_llm_run
from core.model_policy import ModelProfile, ModelRole, ReasoningEffort


class FakeMessages:
    async def create(self, **_payload):
        return SimpleNamespace(
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
