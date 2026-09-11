"""Real PostgreSQL writes through production governed execution adapters.

Transport failures are injected at the tool boundary; no LLM/network provider is
used. A missing receipt remains unknown, matching the production reconciler.
"""
import asyncio
import json
import os
import random
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from application.agent_result import AgentResultStatus
from application.default_capability_registry import build_default_capability_registry
from application.conversation_store import ConversationScope
from application.work_item import ArgumentValue
from application.write_workflow import ApprovalGrant, GovernedWriteRuntime, OperationConflict
from core.identity import TenantId, UserId, ConversationId
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_target_runtime import PostgresOperationLedger
from infrastructure.target_workflow_execution import _ToolPort, _ToolReconciler
from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager
from services.customer_operations import CustomerOperationsService, OrderStatus
from tests.test_write_workflow import _item, _context


SCENARIOS = [name for name in (
    "approval_interrupt", "duplicate_inflight", "timeout_before_commit",
    "timeout_after_commit", "cancel_after_commit",
) for _ in range(40)]
random.Random(20260908).shuffle(SCENARIOS)


@pytest.fixture(scope="module")
def fault_pool(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
    pool.open()
    try:
        yield pool
    finally:
        pool.close()


@pytest.mark.parametrize("case_id,scenario", list(enumerate(SCENARIOS)))
def test_controlled_business_fault(fault_pool, case_id, scenario):
    async def run():
        operation = uuid4().hex
        tenant, user = "recovery-evidence", "customer"
        order_id = "ORDER-" + operation
        owner = CustomerOperationsService(fault_pool, tenant_id=tenant,
            clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc))
        owner.upsert_order(order_id=order_id, user_id=user, item_name="Fault test",
            amount_minor=39900 + case_id, currency="CNY", status=OrderStatus.DELIVERED,
            refundable_until="2026-09-15T00:00:00+00:00")
        action = build_default_capability_registry(tenant).action("refund.request.create:v1")
        item = replace(_item(operation), expected_output_schema=action.receipt_schema_version,
            reconciliation=action.reconciliation,
            target_entity_version=f"order:{order_id}:v1",
            aggregate_ref=f"order:{order_id}", arguments=(
                ArgumentValue.create("order_id", order_id),
                ArgumentValue.create("expected_order_version", 1),
                ArgumentValue.create("reason", f"customer return {case_id}")))
        context = replace(_context(item), trusted_context={"tenant_id": tenant,
            "user_id": user, "conversation_id": operation, "conv_id": operation,
            "request_id": operation})
        scope = ConversationScope(TenantId(tenant), UserId(user), ConversationId(operation))
        from tests.test_write_workflow import accept_work
        accept_work(fault_pool, scope, item)
        events = []
        entered, release = asyncio.Event(), asyncio.Event()

        class FaultBoundary:
            def __init__(self):
                self.injected = False
                self.manager = MCPToolManager(api_key="unused", model="unused")
                for tool in customer_operation_tools(owner):
                    self.manager.register(tool)

            @property
            def read_reuse(self):
                return self.manager.read_reuse

            async def execute_for_agent(self, tool_id, params, **kwargs):
                write = tool_id == "refund_request_create"
                events.append("execute" if write else "reconcile")
                if write and not self.injected:
                    self.injected = True
                    if scenario == "duplicate_inflight":
                        entered.set()
                        await release.wait()
                    if scenario == "timeout_before_commit":
                        raise TimeoutError("injected before business dispatch")
                    result = await self.manager.execute_for_agent(tool_id, params, **kwargs)
                    if scenario in {"timeout_after_commit", "cancel_after_commit"}:
                        assert result.success and str(result.effect_status).lower() == "committed"
                        events.append("receipt_lost")
                        if scenario == "cancel_after_commit":
                            raise asyncio.CancelledError("injected worker cancellation")
                        raise TimeoutError("injected after business commit")
                    return result
                return await self.manager.execute_for_agent(tool_id, params, **kwargs)

        boundary = FaultBoundary()
        grant = ApprovalGrant(item.approval_binding, operation, item.target_entity_version,
                              True, "user:" + user)

        def runtime(approved=True):
            # Independent runtime and PostgreSQL ledger instances on every call.
            return GovernedWriteRuntime(ledger=PostgresOperationLedger(fault_pool, scope),
                tool_port=_ToolPort(boundary, context, principal="billing"),
                reconciliation_port=_ToolReconciler(boundary, context, principal="billing"),
                approval_grants={item.approval_binding: grant} if approved else {})

        observed = []
        if scenario == "approval_interrupt":
            waiting = await runtime(False)(context)
            assert waiting.status is AgentResultStatus.WAITING_APPROVAL
            assert events == []
            observed.append(waiting.status.value)
        if scenario == "duplicate_inflight":
            task = asyncio.create_task(runtime()(context))
            await asyncio.wait_for(entered.wait(), timeout=10)
            try:
                duplicate = await runtime()(context)
                observed.append(duplicate.status.value)
                assert events[:3] == ["execute", "reconcile", "execute"]
            finally:
                release.set()
            try:
                observed.append((await task).status.value)
            except OperationConflict:
                observed.append("CAS_CONFLICT")
        else:
            try:
                observed.append((await runtime()(context)).status.value)
            except asyncio.CancelledError:
                observed.append("WORKER_CANCELLED")

        for _ in range(4):
            result = await runtime()(context)
            observed.append(result.status.value)
            if result.status is AgentResultStatus.SUCCEEDED:
                break
        ledger = PostgresOperationLedger(fault_pool, scope)
        record = ledger.acquire(item)
        with fault_pool.transaction() as connection:
            count = connection.execute("SELECT count(*) AS n FROM "
                "dialogpilot_app.customer_refund_requests WHERE tenant_id=%s AND order_id=%s",
                (tenant, order_id)).fetchone()[0]
        recovered = result.status is AgentResultStatus.SUCCEEDED
        row = {"case_id": case_id, "scenario": scenario, "operation_key": operation,
            "recovered": recovered, "business_rows": count, "events": events,
            "outcomes": observed, "operation_status": record.status.value,
            "attempts": record.attempts, "receipt_id": record.receipt_id}
        output = os.getenv("CONTROLLED_FAULT_ROWS")
        if output:
            with Path(output).open("a") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        assert count <= 1
        assert events.count("execute") == (2 if scenario in {
            "timeout_before_commit", "duplicate_inflight"} else 1)
        assert recovered and count == 1
        replay = await runtime(False)(context)
        assert replay.status is AgentResultStatus.SUCCEEDED
        assert replay.action_receipts == result.action_receipts
        assert record.receipt_id

    asyncio.run(run())
