"""All registered idempotent business owners through the production executor."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from application.agent_result import AgentResultStatus
from application.conversation_store import ConversationScope
from application.default_capability_registry import build_default_capability_registry
from application.work_item import ArgumentValue, ControlMode, WorkPlan
from application.write_workflow import OperationStatus
from application.result_board import ResultBoard
from application.response_assembly import _render_board
from core.identity import TenantId, UserId, ConversationId
from infrastructure.postgres_target_runtime import PostgresOperationLedger
from infrastructure.postgres_ticket_service import PostgresTicketService
from infrastructure.target_workflow_execution import TargetWorkflowExecutor
from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager
from services.customer_operations import CustomerOperationsService, OrderStatus, SecuritySeverity
from tests.test_controlled_business_faults import fault_pool
from tests.test_write_workflow import _item, _context, accept_work


ACTIONS = ("refund.request.create:v1", "order.cancel:v1",
           "order.shipping_address.change:v1", "account.freeze:v1")


def setup(pool, action_ref):
    key = uuid4().hex
    tenant, user = "registered-recovery", "user-" + key
    registry = build_default_capability_registry(tenant)
    action = registry.action(action_ref)
    owner = CustomerOperationsService(pool, tenant_id=tenant,
        clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc))
    order_id = "order-" + key
    owner.upsert_order(order_id=order_id, user_id=user, item_name="recovery",
        amount_minor=500, currency="CNY", status=OrderStatus.DELIVERED
        if action_ref == ACTIONS[0] else OrderStatus.PAID,
        refundable_until="2026-09-15T00:00:00+00:00")
    owner.record_security_event(user_id=user, event_type="login",
        severity=SecuritySeverity.CRITICAL, summary="reported")
    arguments = {"order_id": order_id, "expected_order_version": 1}
    if action_ref == ACTIONS[0]:
        arguments["reason"] = "customer request"
    if action_ref == ACTIONS[2]:
        arguments["new_address"] = "New street 12"
    if action_ref == ACTIONS[3]:
        arguments = {"expected_account_version": 1}
    item = replace(_item(key), control_mode=ControlMode.WORKFLOW,
        owner_agent=action.owner_agent, allowed_tools=(*action.allowed_tool_ids, action.reconciliation.tool_id),
        arguments=tuple(ArgumentValue.create(k, v) for k, v in arguments.items()),
        requirement_ids=action.requirement_ids, risk=action.risk,
        expected_output_schema=action.receipt_schema_version, verification_profile=action.verification_profile,
        flow_ref=action.flow_ref, action_ref=action.ref, reconciliation=action.reconciliation,
        registry_fingerprint=registry.fingerprint, target_entity_version="1",
        aggregate_ref=("account:" + user if action_ref == ACTIONS[3] else "order:" + order_id))
    context = replace(_context(item), trusted_context={"tenant_id": tenant, "user_id": user,
        "conv_id": key, "conversation_id": key, "request_id": key,
        "approval_binding": item.approval_binding,
        "approved_operations": [{"operation_key": key, "action_ref": item.action_ref,
            "target_entity_ref": item.aggregate_ref, "target_entity_version": "1",
            "arguments": {arg.name: arg.value for arg in item.arguments}}]})
    manager = MCPToolManager("unused", model="unused")
    for tool in customer_operation_tools(owner):
        manager.register(tool)
    scope = ConversationScope(TenantId(tenant), UserId(user), ConversationId(key))
    accept_work(pool, scope, item)
    return registry, item, context, manager, scope


@pytest.mark.parametrize("action_ref", ACTIONS)
@pytest.mark.parametrize("fault", ["before_commit", "after_commit", "stale_version", "unavailable"])
def test_registered_owners_recover_or_create_one_manual_review(fault_pool, action_ref, fault):
    async def run():
        registry, item, context, manager, scope = setup(fault_pool, action_ref)
        calls = []

        class Boundary:
            @property
            def read_reuse(self):
                return manager.read_reuse

            async def execute_for_agent(self, tool, params, **kwargs):
                calls.append(tool)
                if fault == "unavailable":
                    raise TimeoutError("service unavailable")
                if len(calls) == 1:
                    if fault == "stale_version":
                        # An independent business update invalidates the approved
                        # version before recovery; unchanged replay must be rejected.
                        with fault_pool.transaction() as conn:
                            conn.execute("UPDATE dialogpilot_app.customer_orders SET version=2 WHERE user_id=%s",
                                         (str(scope.user_id),))
                            conn.execute("UPDATE dialogpilot_app.customer_accounts SET version=2 WHERE user_id=%s",
                                         (str(scope.user_id),))
                    if fault in {"before_commit", "stale_version"}:
                        raise TimeoutError("request not dispatched")
                    result = await manager.execute_for_agent(tool, params, **kwargs)
                    assert result.success
                    raise TimeoutError("lost committed receipt")
                return await manager.execute_for_agent(tool, params, **kwargs)

        boundary = Boundary()
        def executor():
            return TargetWorkflowExecutor(fault_pool, boundary, registry=registry)
        result = await executor()(context)
        record = PostgresOperationLedger(fault_pool, scope).acquire(item)
        before = list(calls)
        replay = await executor()(context)
        assert replay == result or replay.status == result.status
        assert calls == before
        if fault in {"before_commit", "after_commit"}:
            assert result.status is AgentResultStatus.SUCCEEDED
            assert record.status is OperationStatus.COMMITTED
            assert record.recovery_attempts == 1
            assert calls[1] == item.reconciliation.tool_id
            assert not record.manual_ticket_id
        else:
            assert result.status is AgentResultStatus.BLOCKED
            assert record.status is OperationStatus.MANUAL_REVIEW
            assert record.manual_ticket_id
            ticket = PostgresTicketService(fault_pool).get_ticket(record.manual_ticket_id)
            assert ticket.identity_metadata["operation_key"] == item.operation_key
            assert ticket.reason == "WRITE_MANUAL_REVIEW_REQUIRED"
            assert record.recovery_attempts == (1 if fault == "stale_version" else 3)
            assert not result.action_receipts  # Human review is not a business commit.
            board = ResultBoard().evaluate(WorkPlan((item,), item.work_item_id), (result,))
            assert not board.task_completed
            assert "业务结果尚未确认" in _render_board(board)
            with fault_pool.transaction() as conn:
                assert conn.execute("SELECT count(*) FROM dialogpilot_app.handoff_tickets "
                    "WHERE identity_metadata->>'operation_key'=%s", (item.operation_key,)).fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM dialogpilot_app.handoff_ticket_outbox "
                    "WHERE ticket_id=%s", (record.manual_ticket_id,)).fetchone()[0] == 1
    asyncio.run(run())


def test_many_recovery_instances_and_late_original_share_one_business_write(fault_pool):
    async def run():
        registry, item, context, manager, scope = setup(fault_pool, ACTIONS[0])
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Boundary:
            @property
            def read_reuse(self):
                return manager.read_reuse

            async def execute_for_agent(self, tool, params, **kwargs):
                calls.append(tool)
                if len(calls) == 1:
                    entered.set()
                    await release.wait()
                return await manager.execute_for_agent(tool, params, **kwargs)
        boundary = Boundary()
        def executor():
            return TargetWorkflowExecutor(fault_pool, boundary, registry=registry)
        original = asyncio.create_task(executor()(context))
        await asyncio.wait_for(entered.wait(), 5)
        try:
            results = await asyncio.wait_for(asyncio.gather(*(executor()(context) for _ in range(8))), 20)
        finally:
            release.set()
        results.append(await original)
        assert all(result.status is AgentResultStatus.SUCCEEDED for result in results)
        record = PostgresOperationLedger(fault_pool, scope).acquire(item)
        assert record.recovery_attempts == 1 and record.attempts == 2
        assert len({result.action_receipts[0].receipt_id for result in results}) == 1
        with fault_pool.transaction() as conn:
            assert conn.execute("SELECT count(*) FROM dialogpilot_app.customer_refund_requests "
                "WHERE user_id=%s", (str(scope.user_id),)).fetchone()[0] == 1
    asyncio.run(run())
