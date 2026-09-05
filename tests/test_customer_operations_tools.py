"""新增四个生产工具的发现、身份、审批、幂等和回执合同。"""

import asyncio
from datetime import datetime, timezone

from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager, ToolCallStatus, ToolEffectStatus
from services.customer_operations import (
    CustomerOperationsService,
    OrderStatus,
    SecuritySeverity,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def setup_runtime(customer_operations):
    owner = CustomerOperationsService(
        customer_operations.pool,
        tenant_id=customer_operations.tenant_id,
        clock=lambda: NOW,
    )
    owner.upsert_order(
        order_id="order-1",
        user_id="user-1",
        item_name="机械键盘",
        amount_minor=39900,
        currency="CNY",
        status=OrderStatus.DELIVERED,
        refundable_until="2026-09-15T00:00:00+00:00",
    )
    owner.record_security_event(
        user_id="user-1",
        event_type="new_device_login",
        severity=SecuritySeverity.WARNING,
        summary="柏林的新设备登录",
    )
    manager = MCPToolManager(api_key="test", model="test")
    for tool in customer_operation_tools(owner):
        manager.register(tool)
    return owner, manager


def context(user_id="user-1"):
    return {
        "tenant_id": "default",
        "user_id": user_id,
        "conv_id": "conversation-1",
        "request_id": "request-1",
    }


def test_tools_reject_a_context_from_another_tenant(customer_operations):
    _owner, manager = setup_runtime(customer_operations)
    wrong_scope = {**context(), "tenant_id": "other-tenant"}
    result = asyncio.run(manager.execute_for_agent(
        "order_lookup", {"order_id": "order-1"}, agent_type="billing",
        context=wrong_scope,
    ))
    assert not result.success
    assert "tenant" in result.error


def test_tool_discovery_is_agent_scoped_and_never_exposes_user_identity(
    customer_operations,
):
    _owner, manager = setup_runtime(customer_operations)
    billing = {
        item["name"]: item for item in manager.anthropic_tools_for_agent("billing")
    }
    security = {
        item["name"]: item
        for item in manager.anthropic_tools_for_agent("account_security")
    }

    assert set(billing) == {
        "order_lookup",
        "refund_status",
        "refund_eligibility_check",
        "refund_request_create",
    }
    assert set(security) == {
        "account_security_event_list",
        "account_security_state",
        "account_freeze_status",
        "account_freeze",
    }
    for definition in [*billing.values(), *security.values()]:
        assert "user_id" not in definition["input_schema"].get("properties", {})


def test_order_and_security_reads_use_trusted_user_context(customer_operations):
    _owner, manager = setup_runtime(customer_operations)
    own = asyncio.run(
        manager.execute_for_agent(
            "order_lookup",
            {"order_id": "order-1"},
            agent_type="billing",
            context=context(),
        )
    )
    other = asyncio.run(
        manager.execute_for_agent(
            "order_lookup",
            {"order_id": "order-1"},
            agent_type="billing",
            context=context("user-2"),
        )
    )
    security = asyncio.run(
        manager.execute_for_agent(
            "account_security_event_list",
            {},
            agent_type="account_security",
            context=context(),
        )
    )

    assert own.success is True and "user_id" not in own.data
    assert other.success is False and other.data is None
    assert [event["event_type"] for event in security.data] == ["new_device_login"]


def test_refund_write_requires_host_approval_and_returns_committed_receipt(
    customer_operations,
):
    _owner, manager = setup_runtime(customer_operations)
    eligibility = asyncio.run(
        manager.execute_for_agent(
            "refund_eligibility_check",
            {"order_id": "order-1"},
            agent_type="billing",
            context=context(),
        )
    )
    params = {
        "order_id": "order-1",
        "expected_order_version": eligibility.data["order_version"],
        "reason": "用户确认退货",
        "approved": True,
    }
    pending = asyncio.run(
        manager.execute_for_agent(
            "refund_request_create",
            params,
            agent_type="billing",
            context=context(),
            call_id="refund-call-1",
        )
    )
    approved = asyncio.run(
        manager.execute_for_agent(
            "refund_request_create",
            params,
            agent_type="billing",
            context=context(),
            approved=True,
            call_id="refund-call-1",
        )
    )
    retry = asyncio.run(
        manager.execute_for_agent(
            "refund_request_create",
            params,
            agent_type="billing",
            context=context(),
            approved=True,
            call_id="refund-call-1",
        )
    )

    assert pending.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert approved.status == ToolCallStatus.SUCCESS.value
    assert approved.effect_status == ToolEffectStatus.COMMITTED.value
    assert approved.receipt_id == retry.receipt_id
    assert approved.data["created"] is True
    assert retry.data["created"] is False

    status = asyncio.run(
        manager.execute_for_agent(
            "refund_status",
            {"order_id": "order-1"},
            agent_type="billing",
            context=context(),
        )
    )
    assert status.success is True
    assert status.data["refund_id"] == approved.data["refund_id"]
    assert "user_id" not in status.data

    reconciled = asyncio.run(
        manager.execute_for_agent(
            "refund_status",
            {"order_id": "order-1", "operation_key": "refund-call-1"},
            agent_type="billing",
            context=context(),
        )
    )
    unrelated = asyncio.run(
        manager.execute_for_agent(
            "refund_status",
            {"order_id": "order-1", "operation_key": "another-operation"},
            agent_type="billing",
            context=context(),
        )
    )
    assert reconciled.success is True
    assert reconciled.data["operation_key"] == "refund-call-1"
    assert unrelated.success is False


def test_order_cancel_write_and_reconciliation_share_exact_operation_key(
    customer_operations,
):
    owner, manager = setup_runtime(customer_operations)
    order = owner.upsert_order(
        order_id="paid-order",
        user_id="user-1",
        item_name="摄像机",
        amount_minor=129900,
        currency="CNY",
        status=OrderStatus.PAID,
    )
    params = {
        "order_id": order.order_id,
        "expected_order_version": order.version,
    }
    pending = asyncio.run(
        manager.execute_for_agent(
            "order_cancel",
            params,
            agent_type="general",
            context=context(),
            call_id="cancel-call-1",
        )
    )
    committed = asyncio.run(
        manager.execute_for_agent(
            "order_cancel",
            params,
            agent_type="general",
            context=context(),
            approved=True,
            call_id="cancel-call-1",
        )
    )
    reconciled = asyncio.run(
        manager.execute_for_agent(
            "order_cancel_status",
            {"order_id": order.order_id, "operation_key": "cancel-call-1"},
            agent_type="general",
            context=context(),
        )
    )
    other_user = asyncio.run(
        manager.execute_for_agent(
            "order_cancel_status",
            {"order_id": order.order_id, "operation_key": "cancel-call-1"},
            agent_type="general",
            context=context("user-2"),
        )
    )

    assert pending.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert committed.effect_status == ToolEffectStatus.COMMITTED.value
    assert reconciled.success is True
    assert reconciled.data["cancellation_id"] == committed.receipt_id
    assert reconciled.data["operation_key"] == "cancel-call-1"
    assert other_user.success is False


def test_shipping_address_write_and_reconciliation_share_exact_operation_key(
    customer_operations,
):
    owner, manager = setup_runtime(customer_operations)
    order = owner.upsert_order(
        order_id="address-order",
        user_id="user-1",
        item_name="咖啡机",
        amount_minor=69900,
        currency="CNY",
        status=OrderStatus.PAID,
        shipping_address="Berlin, Old Street 1",
    )
    params = {
        "order_id": order.order_id,
        "new_address": "Berlin, New Street 9",
        "expected_order_version": order.version,
    }
    pending = asyncio.run(
        manager.execute_for_agent(
            "shipping_address_change",
            params,
            agent_type="general",
            context=context(),
            call_id="address-call-1",
        )
    )
    committed = asyncio.run(
        manager.execute_for_agent(
            "shipping_address_change",
            params,
            agent_type="general",
            context=context(),
            approved=True,
            call_id="address-call-1",
        )
    )
    reconciled = asyncio.run(
        manager.execute_for_agent(
            "shipping_address_change_status",
            {
                "order_id": order.order_id,
                "new_address": params["new_address"],
                "operation_key": "address-call-1",
            },
            agent_type="general",
            context=context(),
        )
    )

    assert pending.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert committed.effect_status == ToolEffectStatus.COMMITTED.value
    assert reconciled.success is True
    assert reconciled.data["change_id"] == committed.receipt_id
    assert reconciled.data["new_address"] == params["new_address"]


def test_account_freeze_write_and_reconciliation_use_trusted_current_user(
    customer_operations,
):
    _owner, manager = setup_runtime(customer_operations)
    state = asyncio.run(
        manager.execute_for_agent(
            "account_security_state",
            {},
            agent_type="account_security",
            context=context(),
        )
    )
    params = {"expected_account_version": state.data["version"]}
    pending = asyncio.run(
        manager.execute_for_agent(
            "account_freeze",
            params,
            agent_type="account_security",
            context=context(),
            call_id="freeze-call-1",
        )
    )
    committed = asyncio.run(
        manager.execute_for_agent(
            "account_freeze",
            params,
            agent_type="account_security",
            context=context(),
            approved=True,
            call_id="freeze-call-1",
        )
    )
    reconciled = asyncio.run(
        manager.execute_for_agent(
            "account_freeze_status",
            {"operation_key": "freeze-call-1"},
            agent_type="account_security",
            context=context(),
        )
    )
    other_user = asyncio.run(
        manager.execute_for_agent(
            "account_freeze_status",
            {"operation_key": "freeze-call-1"},
            agent_type="account_security",
            context=context("user-2"),
        )
    )

    assert pending.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert committed.effect_status == ToolEffectStatus.COMMITTED.value
    assert reconciled.success is True
    assert reconciled.data["freeze_id"] == committed.receipt_id
    assert reconciled.data["operation_key"] == "freeze-call-1"
    assert other_user.success is False


def test_refund_write_rejects_wrong_agent_before_side_effect(customer_operations):
    _owner, manager = setup_runtime(customer_operations)
    denied = asyncio.run(
        manager.execute_for_agent(
            "refund_request_create",
            {"order_id": "order-1", "expected_order_version": 1, "reason": "test"},
            agent_type="general",
            context=context(),
            approved=True,
        )
    )
    assert denied.status == ToolCallStatus.DENIED.value
    assert denied.effect_status == ToolEffectStatus.NONE.value
