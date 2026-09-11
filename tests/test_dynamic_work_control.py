import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from application.agent_result import AgentResultStatus
from application.conversation_state import WorkControlStatus
from application.conversation_store import ConversationScope
from application.deterministic_resolution import TurnObservations
from application.work_control import WorkSuperseded
from application.write_workflow import OperationStatus, WriteToolOutcome, WriteOutcomeStatus
from core.identity import TenantId, UserId, ConversationId, IdentityFactory
from infrastructure.postgres_target_runtime import PostgresConversationStateStore, PostgresOperationLedger
from tests.test_controlled_business_faults import fault_pool
from tests.test_write_workflow import _item, _context, _grant, _runtime, ToolPort, Reconciler, accept_work


@pytest.mark.parametrize("cancel_first", [True, False])
@pytest.mark.parametrize("through_graph", [False, True])
def test_postgres_submission_and_cancellation_have_one_order(fault_pool, cancel_first, through_graph):
    scope = ConversationScope(TenantId("control-race"), UserId("u"), ConversationId(uuid4().hex))
    item = _item()
    accept_work(fault_pool, scope, item)
    state_store = PostgresConversationStateStore(fault_pool)
    ledger = PostgresOperationLedger(fault_pool, scope)

    def cancel():
        state = state_store.load(scope.tenant_id, scope.user_id, scope.conversation_id)
        assert state_store.compare_and_set(state,
            state.close_work_control(item.control, status=WorkControlStatus.CANCELLED))

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class PausedTool(ToolPort):
            async def execute(self, *args, **kwargs):
                assert ledger.acquire(item).status is OperationStatus.EXECUTING
                entered.set()
                await release.wait()
                return await super().execute(*args, **kwargs)

        tool = PausedTool([WriteToolOutcome(WriteOutcomeStatus.COMMITTED,
            "receipt-1", "refund-receipt-v1", "COMMITTED")])
        runtime = _runtime(tool, Reconciler([]), {item.approval_binding: _grant()}, ledger)
        async def invoke():
            if not through_graph:
                return await runtime(_context(item))
            from application.orchestration_runtime import OrchestrationRuntime
            from application.work_item import WorkPlan
            from application.work_control import WorkControlGuard
            graph = OrchestrationRuntime(direct_executor=runtime, workflow_executor=runtime,
                domain_workers={}, control_guard=WorkControlGuard(state_store))
            board = await graph.execute(WorkPlan((item,), item.work_item_id),
                current_message="continue", trusted_context={"tenant_id": str(scope.tenant_id),
                    "user_id": str(scope.user_id), "conversation_id": str(scope.conversation_id)})
            return board.results[0]
        if cancel_first:
            await asyncio.to_thread(cancel)
            if through_graph:
                assert (await invoke()).status is AgentResultStatus.SUPERSEDED
            else:
                with pytest.raises(WorkSuperseded):
                    await invoke()
            assert ledger.acquire(item).status is OperationStatus.PLANNED
            assert tool.calls == []
        else:
            running = asyncio.create_task(invoke())
            await asyncio.wait_for(entered.wait(), 3)
            await asyncio.to_thread(cancel)
            release.set()
            outcome = await running
            assert outcome.status is AgentResultStatus.SUCCEEDED
            replay = await invoke()
            assert replay.action_receipts == outcome.action_receipts
            assert len(tool.calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("revise", [False, True])
@pytest.mark.parametrize("late_input", [False, True])
def test_new_turn_during_execution_preserves_independent_work(revise, late_input):
    from tests.test_turn_runtime import _manager, _Executor
    from application.work_control import WorkControlGuard

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        original = _Executor()

        async def execute(context):
            nonlocal count
            count += 1
            if count == 1:
                entered.set()
                await release.wait()
                if late_input:
                    from tests.test_target_chat_cutover import _MissingThenReadExecutor
                    return await _MissingThenReadExecutor()(context)
            return await original(context)

        manager = _manager(execute)
        manager._orchestration._control_guard = WorkControlGuard(manager._state_store)
        identity = lambda request: IdentityFactory().create_invocation(tenant_id="tenant-a",
            user_id="user-a", conversation_id="conversation-a", request_id=request)
        a = await manager.prepare(identity("a"), TurnObservations("check my order"))
        running = asyncio.create_task(manager.execute(a))
        await asyncio.wait_for(entered.wait(), 3)
        if late_input:
            release.set()
            await running  # Result exists, but its progress has not committed.
        if revise:
            understand = manager._understanding
            async def correction(*args, **kwargs):
                proposal = await understand(*args, **kwargs)
                return replace(proposal, commands=tuple(replace(command,
                    revises_control_id=a.plan.work.items[0].control.control_id)
                    for command in proposal.commands))
            manager._understanding = correction
        b = await manager.prepare(identity("b"), TurnObservations("check another order"))
        b_result = await manager.execute(b)
        b_result = await manager.commit_progress(b_result, prepared=b)
        b_result = await manager.resolve_followup(b, b_result)
        await manager.commit(b_result)
        release.set()
        a_result = await running
        a_result = await manager.commit_progress(a_result, prepared=a)
        a_result = await manager.resolve_followup(a, a_result)
        await manager.commit(a_result)
        assert b_result.board.results[0].status is AgentResultStatus.SUCCEEDED
        assert a_result.board.results[0].status is (
            AgentResultStatus.SUPERSEDED if revise else
            AgentResultStatus.NEEDS_USER_INPUT if late_input else AgentResultStatus.SUCCEEDED)
        state = manager.load_state(a.invocation)
        assert state.accepts(b.plan.work.items[0].control)
        assert len(state.work_controls) == (1 if revise else 2)
        assert (state.pending_interaction is not None) == (late_input and not revise)
        assert count == 2
    asyncio.run(run())
