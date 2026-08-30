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


def setup_runtime(tmp_path):
    owner = CustomerOperationsService(
        str(tmp_path / "operations.db"), clock=lambda: NOW
    )
    owner.upsert_order(
        order_id="order-1", user_id="user-1", item_name="机械键盘",
        amount_minor=39900, currency="CNY", status=OrderStatus.DELIVERED,
        refundable_until="2026-09-15T00:00:00+00:00",
    )
    owner.record_security_event(
        user_id="user-1", event_type="new_device_login",
        severity=SecuritySeverity.WARNING, summary="柏林的新设备登录",
    )
    manager = MCPToolManager(api_key="test", model="test")
    for tool in customer_operation_tools(owner):
        manager.register(tool)
    return owner, manager


def context(user_id="user-1"):
    return {
        "user_id": user_id,
        "conv_id": "conversation-1",
        "request_id": "request-1",
    }


def test_tool_discovery_is_agent_scoped_and_never_exposes_user_identity(tmp_path):
    _owner, manager = setup_runtime(tmp_path)
    billing = {item["name"]: item for item in manager.anthropic_tools_for_agent("billing")}
    security = {item["name"]: item for item in manager.anthropic_tools_for_agent("account_security")}

    assert set(billing) == {
        "order_lookup", "refund_eligibility_check", "refund_request_create",
    }
    assert set(security) == {"account_security_event_list"}
    for definition in [*billing.values(), *security.values()]:
        assert "user_id" not in definition["input_schema"].get("properties", {})


def test_order_and_security_reads_use_trusted_user_context(tmp_path):
    _owner, manager = setup_runtime(tmp_path)
    own = asyncio.run(manager.execute_for_agent(
        "order_lookup", {"order_id": "order-1"}, agent_type="billing", context=context(),
    ))
    other = asyncio.run(manager.execute_for_agent(
        "order_lookup", {"order_id": "order-1"}, agent_type="billing",
        context=context("user-2"),
    ))
    security = asyncio.run(manager.execute_for_agent(
        "account_security_event_list", {}, agent_type="account_security", context=context(),
    ))

    assert own.success is True and "user_id" not in own.data
    assert other.success is False and other.data is None
    assert [event["event_type"] for event in security.data] == ["new_device_login"]


def test_refund_write_requires_host_approval_and_returns_committed_receipt(tmp_path):
    _owner, manager = setup_runtime(tmp_path)
    eligibility = asyncio.run(manager.execute_for_agent(
        "refund_eligibility_check", {"order_id": "order-1"},
        agent_type="billing", context=context(),
    ))
    params = {
        "order_id": "order-1",
        "expected_order_version": eligibility.data["order_version"],
        "reason": "用户确认退货",
        "approved": True,
    }
    pending = asyncio.run(manager.execute_for_agent(
        "refund_request_create", params, agent_type="billing", context=context(),
        call_id="refund-call-1",
    ))
    approved = asyncio.run(manager.execute_for_agent(
        "refund_request_create", params, agent_type="billing", context=context(),
        approved=True, call_id="refund-call-1",
    ))
    retry = asyncio.run(manager.execute_for_agent(
        "refund_request_create", params, agent_type="billing", context=context(),
        approved=True, call_id="refund-call-1",
    ))

    assert pending.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert approved.status == ToolCallStatus.SUCCESS.value
    assert approved.effect_status == ToolEffectStatus.COMMITTED.value
    assert approved.receipt_id == retry.receipt_id
    assert approved.data["created"] is True
    assert retry.data["created"] is False


def test_refund_write_rejects_wrong_agent_before_side_effect(tmp_path):
    _owner, manager = setup_runtime(tmp_path)
    denied = asyncio.run(manager.execute_for_agent(
        "refund_request_create",
        {"order_id": "order-1", "expected_order_version": 1, "reason": "test"},
        agent_type="general", context=context(), approved=True,
    ))
    assert denied.status == ToolCallStatus.DENIED.value
    assert denied.effect_status == ToolEffectStatus.NONE.value
