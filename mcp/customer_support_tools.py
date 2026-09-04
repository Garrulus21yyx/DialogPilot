"""把持久化工单 Owner 投影为受控的 Agent 工具。

模型只提供业务参数；用户身份、会话、请求、Agent 类型和审批事实均来自
``MCPToolManager`` 的可信执行上下文。这里不实现第二套工单状态机。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

from mcp.tool_manager import Tool, ToolEffectReceipt, ToolEffectStatus, ToolRisk
from services.ticket_service import TicketNotFoundError, TicketPriority, TicketService, TicketStatus


_AGENTS = ("general", "technical", "billing", "account_security", "escalation")


def ticket_tools(service: TicketService) -> Tuple[Tool, ...]:
    """构造共享同一个 ``TicketService`` 的查询、详情和创建工具。"""

    async def list_tickets(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        user_id = _trusted(context, "user_id")
        raw_status = str(params.get("status") or "").strip()
        status = TicketStatus(raw_status) if raw_status else None
        limit = min(max(int(params.get("limit", 10)), 1), 20)
        tickets = await asyncio.to_thread(
            service.list_tickets,
            user_id=user_id,
            status=status,
            limit=limit,
        )
        return [{
            "ticket_id": ticket.ticket_id,
            "status": ticket.status.value,
            "priority": ticket.priority.value,
            "summary": ticket.question,
            "reason": ticket.reason,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
        } for ticket in tickets]

    async def get_ticket(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        user_id = _trusted(context, "user_id")
        ticket_id = _bounded(params.get("ticket_id"), "ticket_id", 128)
        ticket = await asyncio.to_thread(service.get_ticket, ticket_id)
        # 不向模型暴露“工单存在但属于别人”，避免 ticket_id 枚举。
        if ticket.user_id != user_id:
            raise TicketNotFoundError("ticket not found")
        view = await asyncio.to_thread(service.get_ticket_view, ticket_id)
        view.pop("user_id", None)
        view.pop("published_response", None)
        return view

    async def create_ticket(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        user_id = _trusted(context, "user_id")
        conv_id = _trusted(context, "conv_id")
        request_id = _trusted(context, "request_id")
        operation_key = str(
            (context or {}).get("business_operation_key") or request_id
        ).strip()
        summary = _bounded(params.get("summary"), "summary", 1000)
        reason = _bounded(params.get("reason"), "reason", 1000)
        priority = TicketPriority(str(params.get("priority") or TicketPriority.NORMAL.value))
        agent_type = str((context or {}).get("agent_type") or "general")
        intent = str((context or {}).get("intent") or "other")
        ticket, created = await asyncio.to_thread(
            service.create_ticket,
            idempotency_key=f"agent-tool:{user_id}:{conv_id}:{operation_key}",
            user_id=user_id,
            conv_id=conv_id,
            request_id=request_id,
            question=summary,
            published_response="",
            reason=reason,
            priority=priority,
            agent_type=agent_type,
            intent=intent,
            verification_status="tool_approved",
        )
        return ToolEffectReceipt(
            data={
                "created": created,
                "ticket_id": ticket.ticket_id,
                "status": ticket.status.value,
                "priority": ticket.priority.value,
                "message": "人工工单已创建" if created else "该请求已存在人工工单",
            },
            effect_status=ToolEffectStatus.COMMITTED,
            receipt_id=ticket.ticket_id,
        )

    async def ticket_by_operation(
        params: Dict[str, Any], context: Optional[Dict[str, Any]],
    ):
        user_id = _trusted(context, "user_id")
        conv_id = _trusted(context, "conv_id")
        operation_key = _bounded(
            params.get("operation_key"), "operation_key", 512,
        )
        ticket = await asyncio.to_thread(
            service.get_ticket_by_idempotency_key,
            idempotency_key=f"agent-tool:{user_id}:{conv_id}:{operation_key}",
            user_id=user_id,
            conv_id=conv_id,
        )
        return {
            "ticket_id": ticket.ticket_id,
            "operation_key": operation_key,
            "status": ticket.status.value,
            "priority": ticket.priority.value,
            "updated_at": ticket.updated_at,
        }

    return (
        Tool(
            name="support_ticket_list",
            description="查询当前登录用户自己的人工支持工单；可按状态过滤，不要传 user_id",
            handler=list_tickets,
            schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [status.value for status in TicketStatus],
                        "description": "可选工单状态",
                    },
                    "limit": {"type": "integer", "description": "返回数量，1-20"},
                },
            },
            allowed_agents=_AGENTS,
            read_only=True,
            authority="support.ticket_state",
            manifest_version="tool-manifest-v1",
            output_schema_version="ticket-list-v1",
            preconditions=("authenticated_user",),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=("ticket_id", "status", "priority", "summary", "reason", "updated_at"),
        ),
        Tool(
            name="support_ticket_get",
            description="查询当前登录用户某张人工工单的状态和不可变审计历史",
            handler=get_ticket,
            schema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string", "description": "人工工单 ID"},
                },
                "required": ["ticket_id"],
            },
            allowed_agents=_AGENTS,
            read_only=True,
            authority="support.ticket_state",
            manifest_version="tool-manifest-v1",
            output_schema_version="ticket-view-v1",
            preconditions=("authenticated_user", "ticket_id"),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=(
                "ticket_id", "status", "priority", "question", "reason",
                "events", "updated_at",
            ),
        ),
        Tool(
            name="support_ticket_by_operation",
            description=(
                "按宿主提供的原业务 operation key 查询当前用户会话中的人工工单；"
                "仅用于未知写结果对账"
            ),
            handler=ticket_by_operation,
            schema={
                "type": "object",
                "properties": {
                    "operation_key": {
                        "type": "string",
                        "description": "原人工工单创建操作标识",
                    },
                },
                "required": ["operation_key"],
            },
            allowed_agents=_AGENTS,
            read_only=True,
            authority="support.ticket_state",
            manifest_version="tool-manifest-v1",
            output_schema_version="ticket-operation-view-v1",
            preconditions=("authenticated_user", "operation_key"),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=(
                "ticket_id", "operation_key", "status", "priority", "updated_at",
            ),
        ),
        Tool(
            name="support_ticket_create",
            description=(
                "在用户明确要求人工处理且宿主允许审批时创建人工工单；"
                "这是持久化写操作，未获宿主批准时只会返回 awaiting_approval"
            ),
            handler=create_ticket,
            schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "需要人工处理的问题摘要"},
                    "reason": {"type": "string", "description": "为什么自动流程无法完成"},
                    "priority": {
                        "type": "string",
                        "enum": [priority.value for priority in TicketPriority],
                        "description": "人工队列优先级",
                    },
                },
                "required": ["summary", "reason"],
            },
            allowed_agents=_AGENTS,
            risk=ToolRisk.HIGH,
            read_only=False,
            requires_approval=True,
            timeout_s=5.0,
            authority="support.handoff_action",
            manifest_version="tool-manifest-v1",
            output_schema_version="ticket-create-result-v1",
            receipt_schema_version="action-receipt-v1",
            preconditions=("authenticated_user", "explicit_handoff", "approved"),
            idempotency="request_operation_key",
            retry_policy="receipt_reconcile_before_retry",
            typed_outcomes=("COMMITTED", "NOT_COMMITTED", "OUTCOME_UNKNOWN"),
            output_fields=("created", "ticket_id", "status", "priority"),
        ),
    )


def _trusted(context: Optional[Dict[str, Any]], key: str) -> str:
    value = str((context or {}).get(key) or "").strip()
    if not value:
        raise ValueError(f"support ticket tool requires trusted {key} context")
    return value


def _bounded(value: Any, name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return text
