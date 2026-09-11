"""Native waiting graphs retain queued goals, not merely their public labels."""
import asyncio
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import AgentResult, AgentResultStatus
from application.conversation_state import InMemoryConversationStateStore
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_approval_conversation import domain, call
from tests.test_target_persistence_and_manager import _ResumeAwareUnderstanding, _OrderCancellationWorkflowExecutor


def identity(request):
    return IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id=request)


@pytest.mark.parametrize("slots", [2, 3, 4])
def test_run_consumers_allow_planning_during_a_blocked_execution(slots):
    from application.target_run import TargetRunCoordinator
    async def run():
        stop, release, occupied = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Coordinator:
            entered = 0
            async def pump_once(self):
                self.entered += 1
                if self.entered == slots:
                    occupied.set()
                await release.wait()
                return 1
        coordinator = Coordinator()
        serving = asyncio.create_task(TargetRunCoordinator.serve(
            coordinator, stop, concurrency=slots, poll_seconds=0.01))
        try:
            await asyncio.wait_for(occupied.wait(), 1)
            assert coordinator.entered == slots
        finally:
            stop.set()
            release.set()
            await serving
    asyncio.run(run())


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("approve", [False, True])
def test_new_goals_queue_on_native_approval_checkpoint_then_resume(count, approve):
    async def run():
        agent, _, model, reads = domain([call("prepare_order_cancel")])
        calls = []
        async def worker(context):
            calls.append(context.work_item)
            if context.work_item.objective == "Original" and not context.work_item.continuation_of:
                return await agent(context)
            return AgentResult(context.work_item.work_item_id, context.work_item.owner_agent,
                AgentResultStatus.SUCCEEDED, "DONE", "test")
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        write = _OrderCancellationWorkflowExecutor()
        graph = OrchestrationRuntime(direct_executor=worker, workflow_executor=write,
            domain_workers={"order_logistics": worker}, checkpointer=saver)
        understanding = _ResumeAwareUnderstanding(None)
        store = InMemoryConversationStateStore()
        manager = TargetConversationManager(state_store=store, registry=agent._registry,
            understanding=understanding, orchestration=graph)
        def proposal(name):
            return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(name,
                CommandKind.DELEGATE_TASK, "order_logistics", name, allow_action_proposals=True),), "TEST")
        async def execute(prepared):
            prepared = await manager.bind_execution(prepared)
            result = await manager.execute(prepared)
            result = await manager.commit_progress(result, prepared=prepared)
            result = await manager.resolve_followup(prepared, result)
            await manager.commit(result)
            return result
        understanding.initial = proposal("Original")
        first = await execute(manager.accept_before_execution(
            await manager.prepare(identity("first"), TurnObservations("Original"))))
        pending = first.state_after.pending_approval
        # This approval was understood before the other Runs appended their work.
        approval = await manager.prepare(identity("approval"), TurnObservations("",
            approval_id=pending.approval_id, approval_decision=approve))
        for i in range(count):
            understanding.initial = proposal(f"Queued-{i}")
            prepared = manager.accept_before_execution(
                await manager.prepare(identity(f"queued-{i}"), TurnObservations("Another independent task")))
            queued = await execute(prepared)
            assert queued.checkpoint_thread_id == first.checkpoint_thread_id
            assert queued.state_after.pending_approval.operations == pending.operations
            assert queued.state_after.pending_approval.version == pending.version
            assert len(calls) == 1, "queued action-capable workers must not prepare a second approval"
        waiting = manager.load_state(identity("approval")).pending_approval
        assert len(waiting.suspended_work_items) == count + 1
        rebound = await manager.bind_execution(approval)
        assert {item.objective for item in rebound.plan.work.items if item.continuation_of} == {
            "Original", *(f"Queued-{i}" for i in range(count))}
        final = await execute(rebound)
        assert final.state_after.pending_approval is None
        assert len(write.items) == int(approve)
        assert len(reads) == model.calls == 1
        assert sorted(item.objective for item in calls) == sorted([
            "Original", "Original", *(f"Queued-{i}" for i in range(count))])
        snapshot = await graph.graph.aget_state({"configurable": {"thread_id": first.checkpoint_thread_id}})
        assert not snapshot.next
    asyncio.run(run())


def test_waiting_for_execution_resumes_the_saved_planner_step_without_failure_budget():
    from application.target_run import TargetRunWorker
    from application.turn_runtime import TurnRuntime
    from application.response_assembly import ResponseAssembler
    from application.chat_contracts import Completed
    from tests.test_turn_runtime import _manager, _Executor, _CountingManager, _identity
    from tests.test_target_run import _RunStore, _item
    class Store(_RunStore):
        ready = False
        deferred = 0
        def acquire_execution(self, item, *, worker_id):
            self.assert_owned(item, worker_id=worker_id)
            return self.ready
        def defer(self, item, *, worker_id):
            self.assert_owned(item, worker_id=worker_id)
            self.deferred += 1
            self.available = True
            self.attempt += 1
    async def run():
        executor = _Executor()
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        manager = _CountingManager(_manager(executor, checkpointer=saver))
        runtime = TurnRuntime(manager, ResponseAssembler(), checkpointer=saver)
        store = Store(_item())
        async def execute(item, guard):
            await runtime.execute(_identity(), TurnObservations("Query order"))
            return Completed("reply", {"response": "done"})
        worker = TargetRunWorker(store, execute, max_attempts=1)
        for _ in range(5):
            assert await worker.run_once(worker_id="worker") == ()
        assert manager.prepare_calls == 1 and executor.calls == 0
        assert store.deferred == 5 and store.releases == [] and store.terminal_value is None
        store.ready = True
        outcome, = await worker.run_once(worker_id="worker")
        assert isinstance(outcome, Completed)
        assert manager.prepare_calls == executor.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("change", ["unrelated", "new_identity", "new_version"])
def test_semantic_answer_is_bound_to_original_wait_not_whichever_wait_is_current(change):
    from application.agent_result import MissingInputSpec
    from application.conversation_state import ConversationStateConflict
    async def run():
        async def worker(context):
            item = context.work_item
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                    "INPUT", "string", "Which color?"),))
        agent, _, _, _ = domain([])
        state_store = InMemoryConversationStateStore()
        understanding = _ResumeAwareUnderstanding(TurnProposal(ProposalDisposition.RESOLVED,
            (CommandProposal("choose", CommandKind.DELEGATE_TASK, "order_logistics", "Choose color"),), "TEST"))
        graph = OrchestrationRuntime(direct_executor=worker, workflow_executor=worker,
            domain_workers={"order_logistics": worker},
            checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
        manager = TargetConversationManager(state_store=state_store, registry=agent._registry,
            understanding=understanding, orchestration=graph)
        first = await manager.handle(identity("first"), TurnObservations("Choose color"))
        pending = first.state_after.pending_interaction
        class Semantic:
            calls = 0
            async def __call__(self, *args):
                self.calls += 1
                return TurnProposal(ProposalDisposition.RESOLVED, (), "INPUT",
                    input_values=((pending.suspended_work_items[0].work_item_id, "reply", "Blue"),))
        semantic = Semantic()
        manager._understanding = semantic
        prepared = await manager.prepare(identity("answer"), TurnObservations("Blue"))
        assert not manager.accept_before_execution(prepared).acceptance_committed
        live = first.state_after
        revised = replace(live, version=live.version + 1, pending_interaction=replace(pending,
            interaction_id="later-question" if change == "new_identity" else pending.interaction_id,
            version=pending.version + int(change == "new_version")))
        assert state_store.compare_and_set(live, revised)
        if change == "unrelated":
            rebound = await manager.bind_execution(prepared)
            assert rebound.state.pending_interaction is None
        else:
            with pytest.raises(ConversationStateConflict, match="retired interaction"):
                await manager.bind_execution(prepared)
            assert manager.load_state(identity("answer")).pending_interaction == revised.pending_interaction
        assert semantic.calls == 1
    asyncio.run(run())
