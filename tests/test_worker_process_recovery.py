"""Kill a real worker process and reclaim its durable run/operation identity."""
import asyncio
from datetime import datetime, timezone
import multiprocessing
import time
from dataclasses import replace
from typing import TypedDict

import pytest

from application.agent_result import AgentResultStatus
from application.chat_contracts import Completed
from application.target_run import TargetRunWorker, TargetRunClaimLost, TargetRunTerminal
from application.run_execution import acquire_execution, bind_run_execution
from application.default_capability_registry import build_default_capability_registry
from infrastructure.postgres import PostgresPool, PostgresPoolConfig
from infrastructure.postgres_target_run import PostgresTargetRunStore
from infrastructure.target_workflow_execution import TargetWorkflowExecutor
from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager
from services.customer_operations import CustomerOperationsService
from tests.test_postgres_target_run import target_run_components, _admit_and_bind
from tests.test_registered_write_recovery_postgres import setup, ACTIONS
from tests.test_write_workflow import _context
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import PostgresCheckpointOwner
from langgraph.graph import StateGraph, START, END
from application.work_item import WorkItem


class RecoveryState(TypedDict):
    item: WorkItem
    trusted: dict


def _saved_work_graph(saver):
    graph = StateGraph(RecoveryState)
    graph.add_node("prepared_work", lambda state: state)
    graph.add_edge(START, "prepared_work")
    graph.add_edge("prepared_work", END)
    return graph.compile(checkpointer=saver)


def _crashing_worker(url, pipe, after_write):
    pipe.send("process_started")
    pool = PostgresPool(PostgresPoolConfig(url, max_size=2))
    pool.open()
    pipe.send("pool_opened")
    try:
        registry, item, context, manager, scope = setup(pool, ACTIONS[0])
        pipe.send("business_prepared")
        identity = IdentityFactory().create_invocation(tenant_id=str(scope.tenant_id),
            user_id=str(scope.user_id), conversation_id=str(scope.conversation_id),
            request_id=context.trusted_context["request_id"])
        _admit_and_bind(pool, identity=identity, message="申请退款")
        store = PostgresTargetRunStore(pool)
        claim = store.claim(worker_id="worker-a", lease_seconds=10)[0]
        assert store.acquire_execution(claim, worker_id="worker-a")
        pipe.send("execution_acquired")
        with PostgresCheckpointOwner(url, setup=True) as saver:
            _saved_work_graph(saver).invoke({"item": item, "trusted": dict(context.trusted_context)},
                {"configurable": {"thread_id": str(claim.run_id)}})
        pipe.send("checkpoint_saved")
        receipt = None
        if after_write:
            with bind_run_execution(store, claim, "worker-a"):
                result = asyncio.run(TargetWorkflowExecutor(pool, manager, registry=registry)(context))
            assert result.status is AgentResultStatus.SUCCEEDED
            receipt = result.action_receipts[0].receipt_id
        pipe.send((claim, receipt))
        # Deliberately do not acknowledge the run. Parent kills this process.
        multiprocessing.Event().wait(30)
    finally:
        pool.close()


@pytest.mark.parametrize("after_write", [False, True])
def test_process_kill_reclaims_without_duplicate_refund(target_run_components, after_write, record_property):
    pool = target_run_components
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_crashing_worker, args=(pool.config.database_url, child, after_write))
    process.start()
    try:
        deadline, stage = time.monotonic() + 20, "spawn_pending"
        while True:
            assert parent.poll(max(0, deadline - time.monotonic())), (
                f"worker did not reach crash boundary; last stage={stage}; exitcode={process.exitcode}")
            message = parent.recv()
            if not isinstance(message, str):
                stale, receipt = message
                break
            stage = message
        assert PostgresTargetRunStore(pool).claim(worker_id="worker-b", lease_seconds=3) == ()
        process.terminate()
        process.join(5)
        assert not process.is_alive() and process.exitcode != 0
        # Advance the lease boundary in the isolated DB, avoiding a slow sleep.
        with pool.transaction() as conn:
            conn.execute("UPDATE dialogpilot_app.compatibility_execution_outbox "
                "SET lease_until=transaction_timestamp()-interval '1 second' WHERE invocation_key=%s",
                (str(stale.invocation_key),))
        # Reconstruct from PostgreSQL's native checkpoint, not IPC memory.
        with PostgresCheckpointOwner(pool.config.database_url) as saver:
            saved = _saved_work_graph(saver).get_state(
                {"configurable": {"thread_id": str(stale.run_id)}}).values
        context = replace(_context(saved["item"]), trusted_context=saved["trusted"])
        assert context.trusted_context["tenant_id"] == stale.tenant_id
        assert context.trusted_context["user_id"] == stale.user_id
        assert context.trusted_context["conversation_id"] == stale.conversation_id
        owner = CustomerOperationsService(pool, tenant_id=stale.tenant_id,
            clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc))
        tools = MCPToolManager("unused", model="unused")
        for tool in customer_operation_tools(owner):
            tools.register(tool)
        registry = build_default_capability_registry(stale.tenant_id)
        async def execute(item, guard):
            await guard()
            await acquire_execution()
            result = await TargetWorkflowExecutor(pool, tools, registry=registry)(context)
            assert result.status is AgentResultStatus.SUCCEEDED
            if receipt:
                assert result.action_receipts[0].receipt_id == receipt
            return Completed("response", {"response": "refund submitted"})
        store = PostgresTargetRunStore(pool)
        worker = TargetRunWorker(store, execute, lease_seconds=10, heartbeat_seconds=1)
        result = asyncio.run(worker.run_once(worker_id="worker-b"))
        assert len(result) == 1 and isinstance(result[0], Completed)
        with pytest.raises(TargetRunClaimLost):
            store.complete(stale, worker_id="worker-a", terminal=TargetRunTerminal("FAILED", {}, "old"))
        assert store.claim(worker_id="worker-c", lease_seconds=10) == ()
        with pool.transaction() as conn:
            assert conn.execute("SELECT count(*) FROM dialogpilot_app.customer_refund_requests "
                "WHERE user_id=%s", (stale.user_id,)).fetchone() == (1,)
        record_property("process_recovery", f"kill_after_write={after_write}; committed_refunds=1; duplicate_writes=0")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
        child.close()
