"""真实人工工单工具的身份、审批、幂等与回执合同。"""
import asyncio

from mcp.customer_support_tools import ticket_tools
from mcp.tool_manager import (
    MCPToolManager,
    ToolCallStatus,
    ToolEffectStatus,
)
from services.ticket_service import TicketService


def runtime(service: TicketService) -> MCPToolManager:
    manager = MCPToolManager(api_key="test", model="test")
    for tool in ticket_tools(service):
        manager.register(tool)
    return manager


def context(user_id="user-1", request_id="request-1"):
    return {
        "user_id": user_id,
        "conv_id": "conversation-1",
        "request_id": request_id,
        "intent": "human_handoff",
    }


def create_params():
    return {
        "summary": "退款超过承诺时间仍未到账",
        "reason": "自动查询无法确认支付渠道最终状态",
        "priority": "high",
    }


def test_ticket_write_waits_for_host_approval_then_returns_committed_receipt(ticket_service):
    service = ticket_service
    manager = runtime(service)

    pending = asyncio.run(manager.execute_for_agent(
        "support_ticket_create",
        {**create_params(), "approved": True},  # 模型参数不能批准自己。
        agent_type="billing",
        context=context(),
    ))
    approved = asyncio.run(manager.execute_for_agent(
        "support_ticket_create",
        create_params(),
        agent_type="billing",
        context=context(),
        approved=True,
        call_id="host-approved-call",
    ))

    assert pending.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert service.list_tickets(user_id="user-1") == [service.get_ticket(approved.receipt_id)]
    assert approved.status == ToolCallStatus.SUCCESS.value
    assert approved.effect_status == ToolEffectStatus.COMMITTED.value
    assert approved.data["created"] is True
    assert approved.data["ticket_id"] == approved.receipt_id
    assert manager.audit_records()[-1].receipt_id == approved.receipt_id


def test_ticket_persists_host_validated_handoff_contract(ticket_service):
    manager = runtime(ticket_service)
    handoff_contract = '{"schema_version":"handoff-draft-v1"}'

    result = asyncio.run(manager.execute_for_agent(
        "support_ticket_create",
        create_params(),
        agent_type="escalation",
        context={
            **context(),
            "handoff_contract_json": handoff_contract,
        },
        approved=True,
    ))

    ticket = ticket_service.get_ticket(result.receipt_id)
    assert ticket.identity_metadata == {
        "handoff_contract": handoff_contract,
    }


def test_ticket_create_is_idempotent_for_same_request(ticket_service):
    service = ticket_service
    manager = runtime(service)

    first = asyncio.run(manager.execute_for_agent(
        "support_ticket_create", create_params(), agent_type="billing",
        context=context(), approved=True,
    ))
    retry = asyncio.run(manager.execute_for_agent(
        "support_ticket_create", create_params(), agent_type="billing",
        context=context(), approved=True,
    ))

    assert first.receipt_id == retry.receipt_id
    assert first.data["created"] is True
    assert retry.data["created"] is False
    assert len(service.list_tickets(user_id="user-1")) == 1


def test_ticket_read_tools_enforce_trusted_user_ownership(ticket_service):
    service = ticket_service
    manager = runtime(service)
    created = asyncio.run(manager.execute_for_agent(
        "support_ticket_create", create_params(), agent_type="billing",
        context=context(), approved=True,
    ))

    own_list = asyncio.run(manager.execute_for_agent(
        "support_ticket_list", {"limit": 10}, agent_type="general", context=context(),
    ))
    other_list = asyncio.run(manager.execute_for_agent(
        "support_ticket_list", {}, agent_type="general", context=context("user-2"),
    ))
    own_detail = asyncio.run(manager.execute_for_agent(
        "support_ticket_get", {"ticket_id": created.receipt_id},
        agent_type="account_security", context=context(),
    ))
    other_detail = asyncio.run(manager.execute_for_agent(
        "support_ticket_get", {"ticket_id": created.receipt_id},
        agent_type="account_security", context=context("user-2"),
    ))

    assert [item["ticket_id"] for item in own_list.data] == [created.receipt_id]
    assert other_list.data == []
    assert own_detail.data["ticket_id"] == created.receipt_id
    assert "user_id" not in own_detail.data
    assert "published_response" not in own_detail.data
    assert other_detail.success is False
    assert other_detail.data is None


def test_ticket_unknown_outcome_can_be_read_by_exact_operation_key(ticket_service):
    manager = runtime(ticket_service)
    operation_key = "operation:v1:handoff-1"
    trusted = {**context(), "business_operation_key": operation_key}
    created = asyncio.run(manager.execute_for_agent(
        "support_ticket_create",
        create_params(),
        agent_type="escalation",
        context=trusted,
        approved=True,
        call_id=operation_key,
    ))

    reconciled = asyncio.run(manager.execute_for_agent(
        "support_ticket_by_operation",
        {"operation_key": operation_key},
        agent_type="escalation",
        context=context(),
        call_id=f"reconcile:{operation_key}",
    ))
    other_user = asyncio.run(manager.execute_for_agent(
        "support_ticket_by_operation",
        {"operation_key": operation_key},
        agent_type="escalation",
        context=context("user-2"),
    ))

    assert reconciled.success is True
    assert reconciled.authority == "support.ticket_state"
    assert reconciled.data["ticket_id"] == created.receipt_id
    assert reconciled.data["operation_key"] == operation_key
    assert other_user.success is False


def test_tool_context_overwrites_spoofed_agent_identity_and_has_no_user_id_schema(ticket_service):
    service = ticket_service
    manager = runtime(service)
    definitions = {
        item["name"]: item for item in manager.anthropic_tools_for_agent("billing")
    }

    result = asyncio.run(manager.execute_for_agent(
        "support_ticket_create",
        create_params(),
        agent_type="billing",
        context={**context(), "agent_type": "account_security"},
        approved=True,
    ))

    ticket = service.get_ticket(result.receipt_id)
    assert ticket.agent_type == "billing"
    assert "user_id" not in definitions["support_ticket_create"]["input_schema"]["properties"]
    assert set(definitions) >= {
        "support_ticket_list", "support_ticket_get",
        "support_ticket_by_operation", "support_ticket_create",
    }
