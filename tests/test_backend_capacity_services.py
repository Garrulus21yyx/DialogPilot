"""Isolated PostgreSQL + Redis capacity/failure tests (never production writes)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4
from types import SimpleNamespace

import httpx
import pytest
import redis

from application.admission_contract import AdmissionCapacityExceeded, AdmissionCreated, AdmissionExisting
from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.chat_rate_limit import ChatRateLimiter, ChatRateExceeded
from infrastructure.postgres import PostgresPool, PostgresPoolConfig, PostgresCapacityExceeded, PostgresQueryTimeout
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from tests.test_postgres_target_run import target_run_components
from tests.test_postgres_memory_projection import memory_projection_scope
from infrastructure.postgres_memory_projection import PostgresMemoryProjectionReader
from infrastructure.conversation_context_cache import CachedConversationReader, ConversationContextCache


@pytest.fixture
def quota_client():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL required")
    client = redis.Redis.from_url(url, socket_timeout=.2, socket_connect_timeout=.2)
    yield client
    client.close()


def test_quota_across_instances_and_subjects(quota_client):
    a = ChatRateLimiter(quota_client, user_limit="3/minute", tenant_limit="100/minute")
    b = ChatRateLimiter(quota_client, user_limit="3/minute", tenant_limit="100/minute")
    prefix = "test:quota:" + uuid4().hex
    for limiter in (a, b):
        limiter.limiter.storage.key_prefix = prefix
    def hit(i):
        try:
            (a if i % 2 else b).check("t", "u")
            return True
        except ChatRateExceeded:
            return False
    try:
        with ThreadPoolExecutor(16) as executor:
            assert sum(executor.map(hit, range(30))) == 3
        b.check("t", "another-user")
        b.check("another-tenant", "u")
        assert all(0 < quota_client.ttl(k) <= 60 for k in quota_client.scan_iter(prefix + "*"))
    finally:
        for key in quota_client.scan_iter(prefix + "*"):
            quota_client.delete(key)


def test_http_rate_limit_and_dependency_outage(quota_client, monkeypatch):
    from api import main
    from core.auth import Principal
    from application.chat_contracts import Accepted
    limiter = ChatRateLimiter(quota_client, user_limit="1/minute", tenant_limit="100/minute")
    prefix = "test:http-quota:" + uuid4().hex
    limiter.limiter.storage.key_prefix = prefix
    monkeypatch.setattr(main, "_chat_rate_limiter", limiter)
    main.app.dependency_overrides[main.get_principal] = lambda: Principal("signed", frozenset({"chat"}))
    admitted = []
    class App:
        async def handle(self, command):
            admitted.append(command)
            return Accepted("run", {"invocation_key": "invocation"})
    monkeypatch.setattr(main, "_chat_application", lambda: App())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            payload = {"message": "hello", "user_id": "signed", "conv_id": "c", "request_id": "r"}
            assert (await client.post("/chat", json=payload)).status_code == 202
            limited = await client.post("/chat", json=payload)
            assert limited.status_code == 429 and limited.headers["Retry-After"]
            def offline(*args, **kwargs):
                raise redis.ConnectionError("injected outage")
            monkeypatch.setattr(limiter.limiter, "hit", offline)
            unavailable = await client.post("/chat", json=payload)
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"]["code"] == "CHAT_ADMISSION_UNAVAILABLE"
            assert len(admitted) == 1
    try:
        asyncio.run(run())
    finally:
        main.app.dependency_overrides.clear()
        for key in quota_client.scan_iter(prefix + "*"):
            quota_client.delete(key)


def test_postgres_pool_bounds_waiters_and_sql_time(target_run_components, record_property):
    source = target_run_components
    pool = PostgresPool(PostgresPoolConfig(source.config.database_url, min_size=1,
        max_size=1, max_waiting=2, timeout_seconds=.25, statement_timeout_ms=100))
    pool.open()
    def acquire():
        start = time.perf_counter()
        try:
            with pool.transaction() as conn:
                conn.execute("SELECT 1")
            return "ok", time.perf_counter() - start
        except PostgresCapacityExceeded:
            return "busy", time.perf_counter() - start
    try:
        with pool.transaction():
            with ThreadPoolExecutor(16) as executor:
                values = list(executor.map(lambda _: acquire(), range(16)))
            assert all(status == "busy" for status, _ in values)
            stats = pool._pool.get_stats()
            assert stats["requests_waiting"] <= 2
        with pytest.raises(PostgresQueryTimeout):
            with pool.transaction() as conn:
                conn.execute("SELECT pg_sleep(1)")
        with pool.transaction() as conn:
            assert conn.execute("SELECT 1").fetchone() == (1,)
        record_property("pool_metrics", json.dumps({"requests": 16, "rejected": 16,
            "p95_ms": sorted(t * 1000 for _, t in values)[-2],
            "max_connections": 1, "max_waiting": 2,
            "requests_queued": stats.get("requests_queued", 0)}))
    finally:
        pool.close()


def test_durable_admission_cap_is_atomic_and_replayable(target_run_components):
    pool = target_run_components
    prefix = uuid4().hex
    commands = [NewInvocationInbound(IdentityFactory().create_invocation(
        tenant_id="capacity", user_id="user", conversation_id=prefix + str(i),
        request_id="request"), "hello", {}, datetime.now(timezone.utc).isoformat(),
        runtime_kind="target") for i in range(20)]
    def admit(command):
        try:
            return PostgresAdmissionUnitOfWork(pool, max_pending=4).admit_new(command)
        except AdmissionCapacityExceeded:
            return None
    with ThreadPoolExecutor(12) as executor:
        results = list(executor.map(admit, commands))
    assert sum(isinstance(r, AdmissionCreated) for r in results) == 4
    owner = PostgresAdmissionUnitOfWork(pool, max_pending=4)
    for command, result in zip(commands, results):
        if result:
            assert isinstance(owner.admit_new(command), AdmissionExisting)
    with pool.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM dialogpilot_app.workflow_invocations").fetchone() == (4,)
        assert conn.execute("SELECT count(*) FROM dialogpilot_app.conversation_turns").fetchone() == (4,)


def test_admission_lock_timeout_is_http_503_without_partial_acceptance(target_run_components, monkeypatch):
    from api import main
    from core.auth import Principal
    from application.chat_contracts import Accepted
    source = target_run_components
    pool = PostgresPool(PostgresPoolConfig(source.config.database_url, lock_timeout_ms=30))
    pool.open()
    identity = IdentityFactory().create_invocation(tenant_id="lock-test", user_id="u",
        conversation_id=uuid4().hex, request_id="r")
    command = NewInvocationInbound(identity, "hello", {}, datetime.now(timezone.utc).isoformat(), runtime_kind="target")
    class App:
        async def handle(self, _):
            await asyncio.to_thread(PostgresAdmissionUnitOfWork(pool).admit_new, command)
            return Accepted(str(identity.workflow_run_id), {"invocation_key": str(identity.invocation_key)})
    monkeypatch.setattr(main, "_chat_application", lambda: App())
    monkeypatch.setattr(main, "_chat_rate_limiter", SimpleNamespace(check=lambda *args: None))
    main.app.dependency_overrides[main.get_principal] = lambda: Principal("u", frozenset({"chat"}))
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            return await client.post("/chat", json={"message": "hello", "user_id": "u", "conv_id": "c", "request_id": "r"})
    try:
        with source.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (754208190321,))
            response = asyncio.run(request())
            assert response.status_code == 503
            assert conn.execute("SELECT count(*) FROM dialogpilot_app.workflow_invocations").fetchone() == (0,)
        assert asyncio.run(request()).status_code == 202
    finally:
        main.app.dependency_overrides.clear()
        pool.close()


@pytest.mark.parametrize("scenario", ["hot_expiry", "mass_expiry", "redis_outage"])
def test_live_context_cache_failure_load(memory_projection_scope, quota_client, monkeypatch, scenario, record_property):
    pool, _ = memory_projection_scope
    prefix = uuid4().hex
    scopes = [("load-test", prefix, str(i)) for i in range(40)]
    for tenant, user, conv in scopes:
        identity = IdentityFactory().create_invocation(tenant_id=tenant, user_id=user,
            conversation_id=conv, request_id="prior")
        PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(identity,
            "committed context", {}, datetime.now(timezone.utc).isoformat()))
    calls = active = peak = 0
    class Source(PostgresMemoryProjectionReader):
        async def get_projection_result(self, *args, **kwargs):
            nonlocal calls, active, peak
            calls += 1
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(.03)  # fixed latency fault; SQL itself is real
                return await super().get_projection_result(*args, **kwargs)
            finally:
                active -= 1
    cache = ConversationContextCache(quota_client)
    reader = CachedConversationReader(Source(pool), cache, max_inflight=4)
    async def run():
        # Populate canonical snapshots, then expire them explicitly as a burst.
        for scope in scopes:
            value = await PostgresMemoryProjectionReader(pool).get_projection_result(*scope)
            await cache.put(scope, reader.revision(scope), "", value)
            quota_client.pexpire(cache.key(scope), 0)
        if scenario == "redis_outage":
            from redis.retry import Retry
            from redis.backoff import NoBackoff
            # A real refused connection, for reads AND pipeline writes. Do not
            # stop a shared Redis server used by other tests/applications.
            cache.redis = redis.Redis(host="127.0.0.1", port=1,
                socket_timeout=.05, socket_connect_timeout=.05,
                retry=Retry(NoBackoff(), 0))
        latencies = []
        accepted_latencies = []
        async def request(i):
            start = time.perf_counter()
            value = await reader.get_projection_result(*scopes[0 if scenario == "hot_expiry" else i])
            elapsed = 1000 * (time.perf_counter() - start)
            latencies.append(elapsed)
            if not value.reason_codes:
                accepted_latencies.append(elapsed)
            return value
        before = pool._pool.get_stats().get("requests_num", 0)
        values = await asyncio.gather(*(request(i) for i in range(40)))
        rejects = sum(v.reason_codes == ("CONTEXT_SOURCE_BUSY",) for v in values)
        assert calls == (1 if scenario == "hot_expiry" else 4)
        assert rejects == (0 if scenario == "hot_expiry" else 36)
        for value in values:
            if not value.reason_codes:
                assert [m.content for m in value.context.recent_messages] == ["committed context"]
        record_property("live_load_metrics", json.dumps({"scenario": scenario,
            "requests": 40, "source_reads": calls, "peak_source_reads": peak,
            "pg_transactions": pool._pool.get_stats().get("requests_num", 0) - before,
            "rejected": rejects, "p95_ms": sorted(latencies)[37],
            "accepted_p95_ms": sorted(accepted_latencies)[int(.95 * (len(accepted_latencies)-1))],
            "services": "isolated PostgreSQL + Redis; refused-connection fault"}))
    try:
        asyncio.run(run())
    finally:
        if cache.redis is not quota_client:
            cache.redis.close()
        for scope in scopes:
            quota_client.delete(cache.key(scope))
