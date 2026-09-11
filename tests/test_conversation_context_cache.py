"""A cache is disposable; only a certified source snapshot may reach the caller."""
import asyncio
import os
import threading
import uuid
from types import SimpleNamespace

import pytest

from application.memory_projection import MemoryProjectionResult, MemoryProjectionState, MemoryRetrievalOutcome
from infrastructure.conversation_context_cache import CachedConversationReader, ConversationContextCache
from infrastructure.postgres_memory_projection import PostgresMemoryProjectionReader
from memory.conversation_memory import MemoryContext
from tests.test_postgres_memory_projection import memory_projection_scope


class Redis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, *, ex):
        assert ex > 0
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)

    def pipeline(self):
        owner = self
        class Pipeline:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def watch(self, key):
                self.key, self.original = key, owner.get(key)
            def get(self, key):
                return owner.get(key)
            def multi(self):
                pass
            def set(self, *args, **kwargs):
                self.write = args, kwargs
            def execute(self):
                from redis.exceptions import WatchError
                if owner.get(self.key) != self.original:
                    raise WatchError()
                owner.set(*self.write[0], **self.write[1])
        return Pipeline()


def snapshot(text):
    return MemoryProjectionResult(MemoryProjectionState.READY,
        MemoryContext([], [], {}, text, []), 1, {}, MemoryRetrievalOutcome.NOT_NEEDED)


def test_cache_roundtrip_and_all_identity_dimensions():
    async def scenario():
        cache = ConversationContextCache(Redis())
        scope = ('tenant', 'user', 'conversation')
        revision = (1, 0, 1, 2, 1, 'READY', '[]')
        original = snapshot('summary')
        await cache.put(scope, revision, 'request', original)
        assert await cache.get(scope, revision, 'request') == original
        for position in range(3):
            other = list(scope)
            other[position] += '-other'
            assert await cache.get(other, revision, 'request') is None
        for position in range(len(revision)):
            other = list(revision)
            other[position] = str(other[position]) + '-changed'
            assert await cache.get(scope, other, 'request') is None
        assert await cache.get(scope, revision, 'other-request') is None
        await cache.delete_subject(SimpleNamespace(tenant_id=scope[0], user_id=scope[1], conversation_id=scope[2]))
        assert await cache.get(scope, revision, 'request') is None
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['outage', 'corrupt'])
def test_cache_failure_reads_source(failure):
    async def scenario():
        redis = Redis()
        if failure == 'outage':
            def fail(*args, **kwargs):
                raise ConnectionError('offline')
            redis.get = redis.set = fail
        else:
            redis.data[ConversationContextCache.key(('t', 'u', 'c'))] = 'invalid json'
        class Source:
            async def get_projection_result(self, *args, **kwargs):
                return snapshot('source')
        reader = CachedConversationReader(Source(), ConversationContextCache(redis))
        reader.fill = reader.cache.put
        reader.revision = lambda scope: (1,)
        assert (await reader.get_projection_result('t', 'u', 'c')).context.summary == 'source'
    asyncio.run(scenario())


def test_delayed_old_fill_never_becomes_current():
    async def scenario():
        cache = ConversationContextCache(Redis())
        scope = ('t', 'u', 'c')
        await cache.put(scope, (2,), '', snapshot('new'))
        await cache.put(scope, (1,), '', snapshot('old'))
        assert await cache.get(scope, (2,), '') is None
    asyncio.run(scenario())


@pytest.mark.parametrize('deleted', [False, True])
def test_commit_during_cache_hit_restarts_or_revokes(deleted):
    async def scenario():
        cache = ConversationContextCache(Redis())
        await cache.put(('t', 'u', 'c'), (1,), '', snapshot('old'))
        class Source:
            async def get_projection_result(self, *args, **kwargs):
                return snapshot('new')
        reader = CachedConversationReader(Source(), cache)
        reader.fill = cache.put
        calls = 0
        def revision(scope):
            nonlocal calls
            calls += 1
            return (1,) if calls == 1 else (None if deleted else (2,))
        reader.revision = revision
        result = await reader.get_projection_result('t', 'u', 'c')
        if deleted:
            assert result.state is MemoryProjectionState.UNAVAILABLE
            assert not result.context.summary
        else:
            assert result.context.summary == 'new'
    asyncio.run(scenario())


def test_continuously_changing_source_is_typed_unavailable():
    async def scenario():
        class Source:
            async def get_projection_result(self, *args, **kwargs):
                return snapshot('racing')
        reader = CachedConversationReader(Source(), ConversationContextCache(Redis()))
        revisions = iter(range(10))
        reader.revision = lambda scope: (next(revisions),)
        result = await reader.get_projection_result('t', 'u', 'c')
        assert result.reason_codes == ('CONTEXT_SNAPSHOT_CHANGED',)
    asyncio.run(scenario())


def test_postgres_fence_canonical_roundtrip_and_deletion(memory_projection_scope):
    async def scenario():
        pool, identity = memory_projection_scope
        scope = (str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id))
        source = PostgresMemoryProjectionReader(pool)
        cache = ConversationContextCache(Redis())
        reader = CachedConversationReader(source, cache)
        original = await source.get_projection_result(*scope)
        await cache.put(scope, reader.revision(scope), '', original)
        assert await reader.get_projection_result(*scope) == original
        excluded = await reader.get_projection_result(*scope, current_request_id=str(identity.request_id))
        assert not excluded.context.recent_messages
        with pool.transaction() as connection:
            connection.execute('UPDATE dialogpilot_app.conversations SET deleted_at=now(), deletion_epoch=deletion_epoch+1 WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s', scope)
        deleted = await reader.get_projection_result(*scope)
        assert deleted.reason_codes == ('CONVERSATION_UNAVAILABLE',)
        assert not deleted.context.recent_messages
    asyncio.run(scenario())


def test_summary_commit_and_new_turn_invalidate_cached_snapshot(memory_projection_scope):
    from application.conversation_projection import ConversationSubject
    from application.inbound_admission import NewInvocationInbound
    from core.identity import IdentityFactory
    from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
    from infrastructure.postgres_thread_summary import PostgresThreadSummaryRepository
    async def scenario():
        pool, identity = memory_projection_scope
        scope = (str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id))
        source = PostgresMemoryProjectionReader(pool)
        reader = CachedConversationReader(source, ConversationContextCache(Redis()))
        before = await reader.get_projection_result(*scope)
        revision = reader.revision(scope)
        # Force a genuine hit even when other independent projections lag.
        await reader.fill(scope, revision, '', before)
        repo = PostgresThreadSummaryRepository(pool)
        repo.commit(repo.prepare(ConversationSubject(*scope), summarizer_version='cache-test'),
                    summary='Only query; do not submit.')
        assert reader.revision(scope) != revision
        after = await reader.get_projection_result(*scope)
        assert 'Only query; do not submit.' in after.context.summary
        new = IdentityFactory().create_invocation(tenant_id=scope[0], user_id=scope[1],
                conversation_id=scope[2], request_id='newer-request')
        PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
            new, 'new user restriction', {}, '2026-09-11T00:00:00+00:00'))
        final = await reader.get_projection_result(*scope)
        assert final.context.recent_messages[-1].content == 'new user restriction'
        assert 'Only query; do not submit.' in final.context.summary
    asyncio.run(scenario())


def test_cancelled_fill_finishes_before_deletion_can_purge(memory_projection_scope):
    async def scenario():
        pool, identity = memory_projection_scope
        scope = (str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id))
        entered, release, deletion_started, deletion_committed = (threading.Event() for _ in range(4))
        class PausedRedis(Redis):
            def set(self, *args, **kwargs):
                entered.set()
                assert release.wait(5)
                super().set(*args, **kwargs)
        redis = PausedRedis()
        cache = ConversationContextCache(redis)
        reader = CachedConversationReader(PostgresMemoryProjectionReader(pool), cache)
        fill = asyncio.create_task(reader.fill(scope, reader.revision(scope), '', snapshot('private')))
        assert await asyncio.to_thread(entered.wait, 5)
        fill.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fill
        def delete():
            deletion_started.set()
            with pool.transaction() as connection:
                connection.execute('UPDATE dialogpilot_app.conversations SET deleted_at=now(), deletion_epoch=deletion_epoch+1 WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s', scope)
            deletion_committed.set()
            redis.delete(cache.key(scope))
        deletion = asyncio.create_task(asyncio.to_thread(delete))
        try:
            assert await asyncio.to_thread(deletion_started.wait, 5)
            assert not await asyncio.to_thread(deletion_committed.wait, .1)
        finally:
            release.set()
        await deletion
        assert redis.get(cache.key(scope)) is None
        await reader.fill(scope, (1,), '', snapshot('late'))
        assert redis.get(cache.key(scope)) is None
    asyncio.run(scenario())


def test_real_redis_snapshot_roundtrip():
    url = os.getenv('TEST_REDIS_URL')
    if not url:
        pytest.skip('TEST_REDIS_URL required')
    import redis
    client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    cache = ConversationContextCache(client)
    scope = ('test-cache', str(uuid.uuid4()), 'conversation')
    async def scenario():
        try:
            await cache.put(scope, (1,), '', snapshot('canonical'))
            assert await cache.get(scope, (1,), '') == snapshot('canonical')
            assert 0 < client.ttl(cache.key(scope)) <= 300
            assert await cache.get(scope, (2,), '') is None
            # A queued old transaction cannot restore content after deletion.
            with client.pipeline() as pending:
                pending.watch(cache.key(scope))
                pending.get(cache.key(scope))
                pending.multi()
                pending.set(cache.key(scope), 'late private content')
                await cache.delete_subject(SimpleNamespace(
                    tenant_id=scope[0], user_id=scope[1], conversation_id=scope[2]))
                from redis.exceptions import WatchError
                with pytest.raises(WatchError):
                    pending.execute()
            await cache.put(scope, (1,), '', snapshot('late private content'))
            assert client.get(cache.key(scope)) == b'{"deleted":true}'
        finally:
            client.delete(cache.key(scope))
            client.close()
    asyncio.run(scenario())


def test_real_postgres_redis_cache_hit_preserves_canonical_context(memory_projection_scope):
    url = os.getenv('TEST_REDIS_URL')
    if not url:
        pytest.skip('TEST_REDIS_URL required')
    import redis
    pool, identity = memory_projection_scope
    scope = (str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id))
    client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    cache = ConversationContextCache(client)
    source = PostgresMemoryProjectionReader(pool)
    reader = CachedConversationReader(source, cache)
    async def scenario():
        try:
            canonical = await source.get_projection_result(*scope)
            await reader.fill(scope, reader.revision(scope), '', canonical)
            async def must_not_reload(*args, **kwargs):
                raise AssertionError('certified cache hit should not reload transcript')
            source.get_projection_result = must_not_reload
            assert await reader.get_projection_result(*scope) == canonical
        finally:
            client.delete(cache.key(scope))
            client.close()
    asyncio.run(scenario())


def test_synchronous_admission_uses_same_canonical_transcript(memory_projection_scope):
    from application.inbound_admission import NewInvocationInbound
    from application.admission_contract import ExecutionPointer
    from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
    pool, original = memory_projection_scope
    from core.identity import IdentityFactory
    identity = IdentityFactory(lambda: str(uuid.uuid4())).create_invocation(
        tenant_id=str(original.tenant_id), user_id=str(original.user_id),
        conversation_id=str(original.conversation_id), request_id='synchronous-request')
    PostgresAdmissionUnitOfWork(pool).admit_synchronous(
        NewInvocationInbound(identity, 'synchronous user turn', {}, '2026-09-11T00:00:00+00:00'),
        ExecutionPointer('target', 'v1', str(identity.workflow_run_id)))
    result = asyncio.run(PostgresMemoryProjectionReader(pool).get_projection_result(
        str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id)))
    assert [m.content for m in result.context.recent_messages] == ['canonical prior turn', 'synchronous user turn']
