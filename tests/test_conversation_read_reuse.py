"""Sequences cross new goals, processes, permission and mutation boundaries."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langgraph.store.memory import InMemoryStore
from infrastructure.conversation_read_reuse import ConversationReadReuse
from application.default_capability_registry import build_default_capability_registry
from mcp.read_reuse import ReadReusePolicy
from mcp.tool_manager import Tool, ToolEffectReceipt, ToolEffectStatus
from tests.test_target_framework_agent import _manager, _context, _item


def setup(store=None, *, handler=None):
    calls = []
    manager = _manager(calls)
    tool = manager.registered_tools[0]
    tool.task_read_reuse = ReadReusePolicy(300)
    if handler is not None:
        tool.handler = handler
    registry = build_default_capability_registry("tenant-a")
    store = store or InMemoryStore()
    manager.read_reuse = ConversationReadReuse(store, registry)
    return manager, calls, store


async def read(manager, *, context=None, principal="technical", allowed=("catalog_search",)):
    return await manager.execute_for_agent("catalog_search", {"query": "product"},
        agent_type=principal, context=context or _context().trusted_context,
        allowed_tool_ids=allowed)


@pytest.mark.parametrize("scope", ["tenant_id", "user_id", "conversation_id", "agent_type", "permission"])
def test_cache_never_grants_scope_or_permissions(scope):
    async def run():
        manager, calls, _ = setup()
        assert (await read(manager)).success
        context = dict(_context().trusted_context)
        if scope == "agent_type":
            result = await read(manager, principal="other")
        elif scope == "permission":
            result = await read(manager, allowed=())
        else:
            context[scope] = "different"
            result = await read(manager, context=context)
        assert not result.cached
        assert len(calls) == (1 if scope in {"agent_type", "permission"} else 2)
    asyncio.run(run())


def register_writer(manager, handler):
    manager.register(Tool(name="update_record", description="Update a business record", schema={
        "type": "object", "properties": {}}, handler=handler, read_only=False))


async def write(manager, call_id="operation-1"):
    return await manager.execute_for_agent("update_record", {}, agent_type="technical",
        context={**_context().trusted_context, "business_operation_key": call_id},
        call_id=call_id, allowed_tool_ids=("update_record",), approved=True)


@pytest.mark.parametrize("effect", list(ToolEffectStatus))
def test_write_outcome_invalidates_snapshots_and_reconciliation_releases_unknown(effect):
    async def run():
        manager, calls, store = setup()
        await read(manager)
        async def update(params, context):
            return ToolEffectReceipt({"changed": True}, effect, "receipt-1")
        register_writer(manager, update)
        result = await write(manager)
        assert result.effect_status == effect.value
        assert not (await read(manager)).cached
        assert (await read(manager)).cached is (effect in {ToolEffectStatus.COMMITTED, ToolEffectStatus.NOT_COMMITTED})
        if effect not in {ToolEffectStatus.COMMITTED, ToolEffectStatus.NOT_COMMITTED}:
            await manager.read_reuse.resolve_write(_context().trusted_context, "operation-1")
            assert not (await read(manager)).cached
            assert (await read(manager)).cached
    asyncio.run(run())


def test_completed_write_during_cache_lookup_cannot_return_old_snapshot():
    async def run():
        manager, calls, store = setup()
        await read(manager)
        async def update(params, context):
            return ToolEffectReceipt({}, ToolEffectStatus.COMMITTED, "receipt")
        register_writer(manager, update)
        original = manager.read_reuse._epoch
        fired = False
        async def racing_epoch(namespace):
            nonlocal fired
            old = await original(namespace)
            if not fired:
                fired = True
                await write(manager)
            return old
        manager.read_reuse._epoch = racing_epoch
        assert not (await read(manager)).cached
        assert len(calls) == 2
    asyncio.run(run())


def test_overlapping_same_call_id_writers_keep_separate_fences():
    async def run():
        manager, _, store = setup()
        await read(manager)
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        count = 0
        async def update(params, context):
            nonlocal count
            index = count
            count += 1
            entered[index].set()
            await release[index].wait()
            return ToolEffectReceipt({}, ToolEffectStatus.COMMITTED, "receipt")
        register_writer(manager, update)
        a = asyncio.create_task(write(manager))
        await entered[0].wait()
        b = asyncio.create_task(write(manager))
        await entered[1].wait()
        release[0].set()
        await a
        assert not (await read(manager)).cached
        assert not (await read(manager)).cached
        release[1].set()
        await b
        assert not (await read(manager)).cached
        assert (await read(manager)).cached
    asyncio.run(run())


def test_rejected_output_not_persisted_and_original_provenance_retained(monkeypatch):
    from mcp.tool_manager import UntrustedContentGuard, ToolObservationEnvelope
    async def run():
        manager, calls, _ = setup()
        original = UntrustedContentGuard.analyze
        monkeypatch.setattr(UntrustedContentGuard, "analyze", lambda *args: SimpleNamespace(blocked=True))
        assert not (await read(manager)).success
        monkeypatch.setattr(UntrustedContentGuard, "analyze", original)
        assert not (await read(manager)).cached
        assert len(calls) == 2
        async def sourced(params, context):
            return ToolObservationEnvelope({"model": "PX-200"}, ("original-call",))
        other, _, _ = setup(handler=sourced)
        assert (await read(other)).causal_source_call_ids == ("original-call",)
        assert (await read(other)).causal_source_call_ids == ("original-call",)
    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["hit", "save"])
def test_delete_during_cache_hit_and_save_purges_shared_results(boundary):
    async def run():
        deleted, armed = False, False
        class RacingStore(InMemoryStore):
            async def aget(self, namespace, key, **kwargs):
                nonlocal deleted
                item = await super().aget(namespace, key, **kwargs)
                if armed and boundary == "hit" and namespace[-1] == "results":
                    deleted = True
                return item
            async def aput(self, namespace, key, value, **kwargs):
                nonlocal deleted
                await super().aput(namespace, key, value, **kwargs)
                if armed and boundary == "save" and namespace[-1] == "results":
                    deleted = True
        manager, _, store = setup(RacingStore())
        if boundary == "hit":
            await read(manager)
        manager.read_reuse.subject_fence = lambda _: SimpleNamespace(deleted=deleted)
        armed = True
        assert not (await read(manager)).success
        assert not await store.asearch(("target-originals",), limit=100)
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["read", "write"])
def test_post_execution_cache_failure_preserves_result_and_single_audit(operation):
    async def run():
        completed = False
        class FailingStore(InMemoryStore):
            async def aput(self, *args, **kwargs):
                if completed:
                    raise ConnectionError("test snapshot outage")
                await super().aput(*args, **kwargs)
        async def handler(params, context):
            nonlocal completed
            completed = True
            return (ToolEffectReceipt({"done": True}, ToolEffectStatus.COMMITTED, "receipt")
                    if operation == "write" else {"model": "PX-200"})
        manager, _, store = setup(FailingStore(), handler=handler)
        if operation == "write":
            register_writer(manager, handler)
        result = await (write(manager) if operation == "write" else read(manager))
        assert result.success
        assert len(manager.audit_records()) == 1
        if operation == "write":
            assert result.receipt_id == "receipt" and result.effect_status == "committed"
    asyncio.run(run())


def test_reconciliation_cannot_recreate_state_after_subject_deletion():
    async def run():
        deleted = False
        class DeletingStore(InMemoryStore):
            async def aput(self, namespace, key, value, **kwargs):
                nonlocal deleted
                await super().aput(namespace, key, value, **kwargs)
                if key == "epoch":
                    deleted = True
        manager, _, store = setup(DeletingStore())
        manager.read_reuse.subject_fence = lambda _: SimpleNamespace(deleted=deleted)
        await manager.read_reuse.resolve_write(_context().trusted_context, "resolved-operation")
        assert not await store.asearch(("target-originals",), limit=100)
    asyncio.run(run())


def test_direct_query_then_fresh_domain_process_uses_same_durable_owner(postgres_database_url):
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    from infrastructure.target_tool_execution import TargetToolExecutor
    from application.work_item import ControlMode, ArgumentValue
    from tests.test_task_read_continuity import agent, read as model_read
    from tests.test_target_framework_agent import ScriptedToolModel
    from langchain_core.messages import AIMessage
    async def run():
        owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
        async with owner:
            manager, calls, _ = setup(owner.store)
            direct = TargetToolExecutor(manager, registry=manager.read_reuse.registry)
            item = replace(_item(), control_mode=ControlMode.DIRECT,
                arguments=(ArgumentValue.create("query", "product"),))
            assert (await direct(_context(item))).status.value == "SUCCEEDED"
        owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
        async with owner:
            fresh, fresh_calls, _ = setup(owner.store)
            worker = agent(ScriptedToolModel(responses=[model_read("after-restart"), AIMessage(content="Done")]),
                           fresh, owner.store)
            assert (await worker(_context())).status.value == "SUCCEEDED"
            assert not fresh_calls
        assert len(calls) == 1
    asyncio.run(run())


def test_unknown_write_fence_does_not_expire_with_result_cache_after_restart(postgres_database_url):
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    async def run():
        context = {**_context().trusted_context, "conversation_id": "unknown-write-retention"}
        owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
        async with owner:
            manager, _, _ = setup(owner.store)
            async def unknown(params, trusted):
                return ToolEffectReceipt({}, ToolEffectStatus.OUTCOME_UNKNOWN)
            register_writer(manager, unknown)
            result = await manager.execute_for_agent("update_record", {}, agent_type="technical",
                context={**context, "business_operation_key": "pending-write"}, call_id="pending-write",
                approved=True, allowed_tool_ids=("update_record",))
            assert result.effect_status == "outcome_unknown"
            namespace = await manager.read_reuse._namespace(context)
            await owner.store.conn.execute("UPDATE store SET updated_at=NOW()-INTERVAL '60 days' WHERE prefix IN (%s,%s)",
                (".".join(namespace), ".".join((*namespace, "writers"))))
        owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
        async with owner:
            manager, calls, _ = setup(owner.store)
            cursor = await owner.store.conn.execute("SELECT ttl_minutes, expires_at FROM store WHERE prefix IN (%s,%s)",
                (".".join(namespace), ".".join((*namespace, "writers"))))
            assert await cursor.fetchall() == [{"ttl_minutes": None, "expires_at": None}] * 2
            await owner.store.sweep_ttl()
            assert not (await read(manager, context=context)).cached
            assert not (await read(manager, context=context)).cached
            await manager.read_reuse.resolve_write(context, "pending-write")
            assert not (await read(manager, context=context)).cached
            assert (await read(manager, context=context)).cached
            assert len(calls) == 3
    asyncio.run(run())
