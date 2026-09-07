"""Originals follow the existing conversation tombstone and SDK retention."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langgraph.store.memory import InMemoryStore
from infrastructure.target_result_archive import TargetResultArchive, ResultArchiveError
from tests.test_target_framework_agent import _context


@pytest.mark.parametrize("race", [False, True])
def test_deleted_subject_cannot_read_or_repopulate_originals(race):
    async def run():
        deleted = False
        class Store(InMemoryStore):
            async def aput(self, *args, **kwargs):
                nonlocal deleted
                await super().aput(*args, **kwargs)
                if race:
                    deleted = True
        store = Store()
        archive = TargetResultArchive(store, subject_fence=lambda _subject: SimpleNamespace(deleted=deleted))
        context = _context()
        if race:
            with pytest.raises(ResultArchiveError, match="conversation deleted"):
                await archive.save(context, {"content": "original"})
        else:
            ref = await archive.save(context, {"content": "original"})
            deleted = True
            with pytest.raises(ResultArchiveError, match="conversation deleted"):
                await archive.load(context, ref)
        with pytest.raises(ResultArchiveError, match="conversation deleted"):
            await archive.save(context, {"content": "late result"})
        assert await store.asearch(archive.namespace(context)) == []
    asyncio.run(run())


def test_cleanup_is_scoped_and_retryable_after_partial_deletion():
    from application.conversation_projection import ConversationSubject
    async def run():
        class Store(InMemoryStore):
            remaining_before_failure = 10

            async def adelete(self, namespace, key):
                self.remaining_before_failure -= 1
                if self.remaining_before_failure == 0:
                    raise ConnectionError("injected cleanup interruption")
                await super().adelete(namespace, key)

        store = Store()
        archive = TargetResultArchive(store)
        context = _context()
        other = replace(context, trusted_context={**context.trusted_context, "user_id": "other"})
        for i in range(103):
            await archive.save(context, {"content": str(i)})
        ref = await archive.save(other, {"content": "unrelated"})
        subject = ConversationSubject(*(context.trusted_context[key] for key in
            ("tenant_id", "user_id", "conversation_id")))
        with pytest.raises(ConnectionError):
            await archive.delete_subject(subject)
        assert len(await store.asearch(archive.namespace(context), limit=200)) == 94
        await archive.delete_subject(subject)
        assert await store.asearch(archive.namespace(context)) == []
        assert (await archive.load(other, ref))["content"] == "unrelated"
    asyncio.run(run())


def test_sdk_expiry_rejects_unswept_original_and_reclaims_it(postgres_database_url):
    import psycopg
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    async def run():
        owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
        async with owner:
            archive = TargetResultArchive(owner.store)
            context = _context()
            ref = await archive.save(context, {"content": "expiring fixture"})
            with psycopg.connect(postgres_database_url, autocommit=True) as connection:
                connection.execute("UPDATE store SET expires_at=NOW()-INTERVAL '1 minute' WHERE key=%s", (ref,))
            with pytest.raises(ResultArchiveError):
                await archive.load(context, ref)
            assert await owner.store.sweep_ttl() >= 1
    asyncio.run(run())
