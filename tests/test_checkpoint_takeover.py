"""One existing execution graph takes over the conversation's two paused waits."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
from application.orchestration_runtime import OrchestrationRuntime, OrchestrationRuntimeError
from application.work_item import WorkPlan, WorkControlBinding
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_work_control import _item


@pytest.mark.parametrize("crash_after_import", [False, True])
@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_takeover_preserves_colliding_local_ids_and_replays_without_source(crash_after_import, backend, request):
    async def run(saver):
        scope = {"tenant_id": "tenant", "user_id": "user", "conversation_id": "conversation"}
        seen = []
        crash = crash_after_import
        async def worker(context):
            nonlocal crash
            item = context.work_item
            seen.append((item.control.control_id, item.control.revision))
            if item.control.control_id in {"a", "b"} and item.control.revision == 1:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Choose an option"),),
                    working_messages=({"role": "user", "content": item.control.control_id},))
            if item.control.control_id == "a" and crash:
                crash = False
                raise RuntimeError("process boundary after durable import")
            if item.control.control_id in {"a", "b"} and item.control.revision == 2:
                assert context.working_messages == ({"role": "user", "content": item.control.control_id},)
            if item.control.control_id == "queued":
                assert len(context.dependency_results) == 1
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, "DONE", "test")
        def runtime():
            return OrchestrationRuntime(direct_executor=worker,
                domain_workers={"product_technical": worker}, checkpointer=saver)
        a = replace(_item("a", 1, work_item_id="local-root"), requirement_ids=())
        b = replace(a, control=WorkControlBinding("b", 1))
        done_a = replace(a, work_item_id="local-done", control=WorkControlBinding("done-a", 1))
        done_b = replace(done_a, control=WorkControlBinding("done-b", 1))
        queued = replace(b, work_item_id="queued", control=WorkControlBinding("queued", 1), dependencies=(b.work_item_id,))
        primary, source = uuid4().hex, uuid4().hex
        await runtime().execute(WorkPlan((a, done_a), a.work_item_id), thread_id=primary,
                                current_message="first", trusted_context=scope)
        await runtime().execute(WorkPlan((b, done_b, queued), b.work_item_id), thread_id=source,
                                current_message="second", trusted_context=scope)
        new_a = replace(a, work_item_id="new-a", control=WorkControlBinding("a", 2), continuation_of=a.work_item_id)
        new_b = replace(b, work_item_id="new-b", control=WorkControlBinding("b", 2),
                        continuation_of=b.work_item_id, dependencies=(new_a.work_item_id,))
        new_q = replace(queued, work_item_id="new-q", control=WorkControlBinding("queued", 2),
                        continuation_of=queued.work_item_id, dependencies=(new_b.work_item_id,))
        plan = WorkPlan((new_a, new_b, new_q), new_a.work_item_id)
        args = dict(thread_id=primary, source_thread_ids=(source,), current_message="both replies", trusted_context=scope,
                    interrupt_after_completion=True)
        if crash_after_import:
            with pytest.raises(RuntimeError, match="durable import"):
                await runtime().resume(plan, **args)
            snapshot = await runtime().graph.aget_state({"configurable": {"thread_id": primary}})
            assert snapshot.values["work_plan"] == plan
            assert snapshot.values["continuation_messages"]["new-b"]
            # The source is no longer necessary after import has checkpointed.
            await runtime().cancel_interrupt(thread_id=source)
        board = await runtime().resume(plan, **args)
        assert board.task_completed
        assert {item.control.control_id for item, _ in board.retained_outcomes} == {"done-a", "done-b"}
        assert all(result.status is AgentResultStatus.SUCCEEDED for _, result in board.retained_outcomes)
        assert seen.count(("done-a", 1)) == seen.count(("done-b", 1)) == 1
        assert seen.count(("b", 2)) == seen.count(("queued", 2)) == 1
        before_replay = list(seen)
        await runtime().cancel_interrupt(thread_id=source)
        assert await runtime().resume(plan, **args) == board
        assert seen == before_replay
        # A subsequent plan/interaction repair also uses the transferred progress,
        # not a second import from a source that has already been closed.
        next_a = replace(new_a, work_item_id="next-a", control=WorkControlBinding("a", 3), continuation_of=new_a.work_item_id)
        continued = await runtime().resume(WorkPlan((next_a,), next_a.work_item_id), **args)
        assert continued.task_completed
        assert seen.count(("a", 3)) == 1
    if backend == "memory":
        asyncio.run(run(InMemorySaver(serde=target_checkpoint_serializer())))
    else:
        from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
        url = request.getfixturevalue("postgres_database_url")
        async def postgres():
            async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                await run(saver)
        asyncio.run(postgres())


@pytest.mark.parametrize("mismatch", ["tenant", "registry", "not_paused", "unrelated", "replay_source"])
def test_takeover_rejects_unowned_source_before_dispatch(mismatch):
    async def run():
        seen = []
        async def worker(context):
            item = context.work_item
            seen.append(item.control.revision)
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id, "INPUT", "string", "Choose"),))
        runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={"product_technical": worker},
            checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
        a = replace(_item("a", 1, work_item_id="a"), requirement_ids=())
        b = replace(_item("b", 1, work_item_id="b"), requirement_ids=(),
                    registry_fingerprint="other" if mismatch == "registry" else a.registry_fingerprint)
        scope = {"tenant_id": "t", "user_id": "u", "conversation_id": "c"}
        await runtime.execute(WorkPlan((a,), "a"), thread_id="primary", current_message="", trusted_context=scope)
        await runtime.execute(WorkPlan((b,), "b"), thread_id="source", current_message="",
            trusted_context={**scope, "tenant_id": "other"} if mismatch == "tenant" else scope)
        if mismatch == "not_paused":
            await runtime.cancel_interrupt(thread_id="source")
        new = replace(a, work_item_id="new", control=WorkControlBinding("a", 2), continuation_of="a")
        items = (new,) if mismatch == "unrelated" else (new, replace(b, work_item_id="new-b",
            control=WorkControlBinding("b", 2), continuation_of="b", registry_fingerprint=a.registry_fingerprint))
        with pytest.raises(OrchestrationRuntimeError):
            await runtime.resume(WorkPlan((a,), "a") if mismatch == "replay_source" else WorkPlan(items, "new"),
                                 thread_id="primary", source_thread_ids=("source",), current_message="")
        assert seen == [1, 1]
    asyncio.run(run())


def test_closing_one_goal_keeps_the_shared_wait_and_its_progress():
    async def run():
        calls = []
        async def worker(context):
            item = context.work_item
            calls.append(item.control)
            if item.control.revision == 2:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, "DONE", "test")
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id, "INPUT", "string", "Choose"),))
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={"product_technical": worker}, checkpointer=saver)
        a = replace(_item("a", 1, work_item_id="a"), requirement_ids=())
        b = replace(_item("b", 1, work_item_id="b"), requirement_ids=())
        await runtime.execute(WorkPlan((a, b), "a"), thread_id="shared", current_message="initial")
        for _ in range(2):
            board = await runtime.cancel_interrupt(thread_id="shared", closed_work_items=(a,), retain_wait=True)
            assert [result.status for result in board.results] == [AgentResultStatus.CANCELLED, AgentResultStatus.NEEDS_USER_INPUT]
            snapshot = await runtime.graph.aget_state({"configurable": {"thread_id": "shared"}})
            assert any(task.interrupts for task in snapshot.tasks)
        continued = replace(b, work_item_id="new-b", continuation_of="b", control=WorkControlBinding("b", 2))
        board = await runtime.resume(WorkPlan((continued,), continued.work_item_id), thread_id="shared", current_message="blue")
        assert calls == [a.control, b.control, continued.control]
        assert board.retained_outcomes[0][1].status is AgentResultStatus.CANCELLED
        assert board.results[0].status is AgentResultStatus.SUCCEEDED
    asyncio.run(run())
