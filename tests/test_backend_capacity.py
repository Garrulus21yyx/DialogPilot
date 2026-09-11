"""Positive capacity, cancellation, expiry and identity invariants; no LLM calls."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from core import framework_models
from infrastructure.conversation_context_cache import CachedConversationReader, ConversationContextCache
from tests.test_conversation_context_cache import Redis, snapshot


@pytest.mark.parametrize("outage", [False, True])
@pytest.mark.parametrize("distinct", [False, True])
def test_cache_load_is_bounded_and_coalesced(outage, distinct, record_property):
    async def run():
        cache = ConversationContextCache(Redis())
        if outage:
            def fail(*args, **kwargs):
                raise ConnectionError("injected Redis outage")
            cache.redis.get = fail
        calls = active = peak = revisions = 0
        latencies = []
        class Source:
            async def get_projection_result(self, *args, **kwargs):
                nonlocal calls, active, peak
                calls += 1
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(.03)
                active -= 1
                return snapshot(args[2])
        reader = CachedConversationReader(Source(), cache, max_inflight=4, max_callers=128)
        def revision(scope):
            nonlocal revisions
            revisions += 1
            return (1,)
        reader.revision = revision
        reader.fill = cache.put
        # Explicitly expire all old entries; no dependency on wall-clock sleep.
        for i in range(100):
            cache.redis.set(cache.key(("t", "u", str(i))), "expired", ex=1)
        cache.redis.data.clear()
        async def request(i):
            start = time.perf_counter()
            result = await reader.get_projection_result("t", "u", str(i) if distinct else "0")
            latencies.append((time.perf_counter() - start) * 1000)
            return result
        results = await asyncio.gather(*(request(i) for i in range(100)))
        rejects = sum(r.reason_codes == ("CONTEXT_SOURCE_BUSY",) for r in results)
        assert calls == (4 if distinct else 1)
        assert rejects == (96 if distinct else 0)
        assert peak <= 4
        assert revisions <= calls * 3
        assert not reader._inflight and reader._callers == 0
        # No sticky failure/negative cache: next request is admitted and fresh.
        assert not (await reader.get_projection_result("t", "u", "after")).reason_codes
        metrics = {"requests": 100, "source_calls": calls - 1,
            "peak_source_concurrency": peak, "rejected": rejects,
            "p95_ms": sorted(latencies)[94], "mode": "injected-boundary-load"}
        record_property("load_metrics", json.dumps(metrics))
    asyncio.run(run())


def test_cancelled_waiter_does_not_cancel_or_release_shared_read():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class Source:
            async def get_projection_result(self, *args, **kwargs):
                entered.set()
                await release.wait()
                return snapshot("done")
        reader = CachedConversationReader(Source(), None, max_inflight=1)
        reader.revision = lambda scope: (1,)
        first = asyncio.create_task(reader.get_projection_result("t", "u", "c"))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert len(reader._inflight) == 1
        assert (await reader.get_projection_result("t", "u", "other")).reason_codes == ("CONTEXT_SOURCE_BUSY",)
        second = asyncio.create_task(reader.get_projection_result("t", "u", "c"))
        await asyncio.sleep(0)
        release.set()
        assert (await second).context.summary == "done"
        assert not reader._inflight
    asyncio.run(run())


def test_shared_results_are_not_shared_mutable_context():
    async def run():
        class Source:
            async def get_projection_result(self, *args, **kwargs):
                await asyncio.sleep(.01)
                return snapshot("done")
        reader = CachedConversationReader(Source(), None)
        reader.revision = lambda scope: (1,)
        a, b = await asyncio.gather(*(reader.get_projection_result("t", "u", "c") for _ in range(2)))
        a.context.user_profile["untrusted"] = "value"
        assert b.context.user_profile == {}
    asyncio.run(run())


def test_ttl_jitter_stays_in_configured_bounds(monkeypatch):
    expiries = []
    cache = ConversationContextCache(Redis(), ttl_seconds=300, jitter_seconds=60)
    original = cache.redis.set
    def capture(*args, **kwargs):
        expiries.append(kwargs["ex"])
        return original(*args, **kwargs)
    cache.redis.set = capture
    for jitter in (0, 60):
        monkeypatch.setattr("infrastructure.conversation_context_cache.random.randint", lambda a, b: jitter)
        cache.put_sync(("t", "u", "c"), (1,), "", snapshot("ok"))
    assert expiries == [300, 360]


def test_model_capacity_is_shared_released_and_does_not_send_rejected_calls(monkeypatch):
    monkeypatch.setenv("MODEL_MAX_CONCURRENT", "2")
    framework_models._model_slots.cache_clear()
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def model():
            nonlocal calls
            calls += 1
            if calls == 2:
                entered.set()
            await release.wait()
            return "ok"
        active = [asyncio.create_task(framework_models.invoke_model(model(), stage=s))
                  for s in ("planner", "domain")]
        await entered.wait()
        with pytest.raises(framework_models.ModelInvocationError) as error:
            await framework_models.invoke_model(model(), stage="assembler")
        assert error.value.error_type == "ModelCapacityExceeded" and error.value.retryable
        assert calls == 2
        active[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await active[0]
        release.set()
        assert await active[1] == "ok"
        assert await framework_models.invoke_model(model(), stage="recovery") == "ok"
    try:
        asyncio.run(run())
    finally:
        framework_models._model_slots.cache_clear()


def test_http_admission_maps_quota_and_storage_failures(monkeypatch):
    from api import main
    from core.auth import Principal
    from fastapi import HTTPException
    from infrastructure.chat_rate_limit import ChatRateExceeded
    from redis.exceptions import ConnectionError
    principal = Principal("signed-user", frozenset({"chat"}))
    for error, status in ((ChatRateExceeded("CHAT_RATE_EXCEEDED"), 429),
                          (ConnectionError("offline"), 503)):
        def check(*args):
            raise error
        monkeypatch.setattr(main, "_chat_rate_limiter", SimpleNamespace(check=check))
        with pytest.raises(HTTPException) as failure:
            main._admitted_chat_principal(principal)
        assert failure.value.status_code == status
        assert failure.value.headers["Retry-After"]


def test_sdk_transport_retries_are_not_multiplied_by_structured_retry():
    import anthropic
    import httpx2 as httpx
    from langchain_core.messages import HumanMessage
    from core.model_policy import ModelProfile
    from core.structured_model import structured_call
    async def run():
        calls = 0
        async def fail(request):
            nonlocal calls
            calls += 1
            return httpx.Response(500, json={"type": "error", "error": {
                "type": "api_error", "message": "injected service failure"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            sdk = anthropic.AsyncAnthropic(api_key="test", http_client=client, max_retries=2)
            sdk._calculate_retry_timeout = lambda *args: 0
            model = framework_models.framework_model(ModelProfile("test-model"), {"api_key": "test"})
            model.__dict__["_async_client"] = sdk
            with pytest.raises(framework_models.ModelInvocationError):
                await structured_call(model, name="check", schema={"type": "object"},
                    system="test", messages=[HumanMessage("test")], protocol_attempts=2)
            assert calls == 3  # first attempt + two SDK retries, not 3 x 2
    asyncio.run(run())
