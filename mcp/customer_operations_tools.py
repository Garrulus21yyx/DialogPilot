"""把客户业务 Owner 投影为受控的订单、退款与账户工具。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

from mcp.tool_manager import Tool, ToolEffectReceipt, ToolEffectStatus, ToolRisk
from services.customer_operations import (
    CustomerOperationsService,
    SecuritySeverity,
)


def customer_operation_tools(service: CustomerOperationsService) -> Tuple[Tool, ...]:
    """构造订单、退款和安全事件工具；身份与审批只来自执行上下文。"""

    async def order_lookup(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        order = await asyncio.to_thread(
            service.get_order_for_user,
            user_id=_trusted(context, "user_id"),
            order_id=_bounded(params.get("order_id"), "order_id", 128),
        )
        data = order.to_dict()
        data.pop("user_id", None)
        return data

    async def refund_eligibility(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        result = await asyncio.to_thread(
            service.check_refund_eligibility,
            user_id=_trusted(context, "user_id"),
            order_id=_bounded(params.get("order_id"), "order_id", 128),
        )
        return result.to_dict()

    async def refund_status(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        user_id = _trusted(context, "user_id")
        order_id = _bounded(params.get("order_id"), "order_id", 128)
        operation_key = str(params.get("operation_key") or "").strip()
        if operation_key:
            conv_id = _trusted(context, "conv_id")
            result = await asyncio.to_thread(
                service.get_refund_status_for_operation,
                user_id=user_id,
                order_id=order_id,
                idempotency_key=f"refund-tool:{user_id}:{conv_id}:{operation_key}",
            )
        else:
            result = await asyncio.to_thread(
                service.get_refund_status,
                user_id=user_id,
                order_id=order_id,
            )
        data = result.to_dict()
        data.pop("user_id", None)
        if operation_key:
            data["operation_key"] = operation_key
        return data

    async def refund_create(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        user_id = _trusted(context, "user_id")
        conv_id = _trusted(context, "conv_id")
        tool_call_id = _trusted(context, "tool_call_id")
        refund, created = await asyncio.to_thread(
            service.create_refund_request,
            idempotency_key=f"refund-tool:{user_id}:{conv_id}:{tool_call_id}",
            user_id=user_id,
            order_id=_bounded(params.get("order_id"), "order_id", 128),
            expected_order_version=int(params.get("expected_order_version")),
            reason=_bounded(params.get("reason"), "reason", 1000),
        )
        return ToolEffectReceipt(
            data={
                "created": created,
                "refund_id": refund.refund_id,
                "order_id": refund.order_id,
                "status": refund.status.value,
                "amount_minor": refund.amount_minor,
                "currency": refund.currency,
                "message": "退款申请已提交" if created else "该退款申请已存在",
            },
            effect_status=ToolEffectStatus.COMMITTED,
            receipt_id=refund.refund_id,
        )

    async def order_cancel(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        user_id = _trusted(context, "user_id")
        conv_id = _trusted(context, "conv_id")
        tool_call_id = _trusted(context, "tool_call_id")
        cancellation, created = await asyncio.to_thread(
            service.cancel_order,
            idempotency_key=(
                f"order-cancel-tool:{user_id}:{conv_id}:{tool_call_id}"
            ),
            user_id=user_id,
            order_id=_bounded(params.get("order_id"), "order_id", 128),
            expected_order_version=int(params.get("expected_order_version")),
        )
        return ToolEffectReceipt(
            data={
                "created": created,
                "cancellation_id": cancellation.cancellation_id,
                "order_id": cancellation.order_id,
                "status": cancellation.status,
                "order_version": cancellation.order_version,
                "message": "订单已取消" if created else "该取消操作已完成",
            },
            effect_status=ToolEffectStatus.COMMITTED,
            receipt_id=cancellation.cancellation_id,
        )

    async def order_cancel_status(
        params: Dict[str, Any], context: Optional[Dict[str, Any]],
    ):
        user_id = _trusted(context, "user_id")
        conv_id = _trusted(context, "conv_id")
        order_id = _bounded(params.get("order_id"), "order_id", 128)
        operation_key = _bounded(
            params.get("operation_key"), "operation_key", 512,
        )
        cancellation = await asyncio.to_thread(
            service.get_order_cancellation_for_operation,
            user_id=user_id,
            order_id=order_id,
            idempotency_key=(
                f"order-cancel-tool:{user_id}:{conv_id}:{operation_key}"
            ),
        )
        data = cancellation.to_dict()
        data.pop("user_id", None)
        data["operation_key"] = operation_key
        return data

    async def shipping_address_change(
        params: Dict[str, Any], context: Optional[Dict[str, Any]],
    ):
        user_id = _trusted(context, "user_id")
        conv_id = _trusted(context, "conv_id")
        tool_call_id = _trusted(context, "tool_call_id")
        change, created = await asyncio.to_thread(
            service.change_shipping_address,
            idempotency_key=(
                f"address-change-tool:{user_id}:{conv_id}:{tool_call_id}"
            ),
            user_id=user_id,
            order_id=_bounded(params.get("order_id"), "order_id", 128),
            expected_order_version=int(params.get("expected_order_version")),
            new_address=_bounded(params.get("new_address"), "new_address", 500),
        )
        return ToolEffectReceipt(
            data={
                "created": created,
                "change_id": change.change_id,
                "order_id": change.order_id,
                "new_address": change.new_address,
                "status": change.status,
                "order_version": change.order_version,
                "message": "收货地址已修改" if created else "该地址修改已完成",
            },
            effect_status=ToolEffectStatus.COMMITTED,
            receipt_id=change.change_id,
        )

    async def shipping_address_change_status(
        params: Dict[str, Any], context: Optional[Dict[str, Any]],
    ):
        user_id = _trusted(context, "user_id")
        conv_id = _trusted(context, "conv_id")
        order_id = _bounded(params.get("order_id"), "order_id", 128)
        operation_key = _bounded(
            params.get("operation_key"), "operation_key", 512,
        )
        change = await asyncio.to_thread(
            service.get_shipping_address_change_for_operation,
            user_id=user_id,
            order_id=order_id,
            idempotency_key=(
                f"address-change-tool:{user_id}:{conv_id}:{operation_key}"
            ),
        )
        data = change.to_dict()
        data.pop("user_id", None)
        data["operation_key"] = operation_key
        return data

    async def security_events(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        raw_severity = str(params.get("severity") or "").strip()
        severity = SecuritySeverity(raw_severity) if raw_severity else None
        limit = min(max(int(params.get("limit", 10)), 1), 20)
        return await asyncio.to_thread(
            service.list_security_events,
            user_id=_trusted(context, "user_id"),
            severity=severity,
            limit=limit,
        )

    order_schema = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "用户提供的订单 ID"},
        },
        "required": ["order_id"],
    }
    refund_status_schema = {
        "type": "object",
        "properties": {
            **order_schema["properties"],
            "operation_key": {
                "type": "string",
                "description": "宿主在未知写结果对账时注入的原操作标识",
            },
        },
        "required": ["order_id"],
    }
    return (
        Tool(
            name="order_lookup",
            description=(
                "读取当前认证用户的订单状态、金额和版本；用于回答物流或退款前核对，"
                "不要传 user_id，也不能据此宣称已退款"
            ),
            handler=order_lookup,
            schema=order_schema,
            allowed_agents=("general", "billing", "technical"),
            read_only=True,
            authority="order.current_state",
            manifest_version="tool-manifest-v1",
            output_schema_version="order-view-v1",
            preconditions=("authenticated_user", "order_id"),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=("order_id", "status", "amount_minor", "currency", "version", "updated_at"),
        ),
        Tool(
            name="refund_status",
            description=(
                "按当前认证用户和订单 ID 读取退款申请的当前权威状态；"
                "operation_key 仅供宿主对账时绑定原操作"
            ),
            handler=refund_status,
            schema=refund_status_schema,
            allowed_agents=("general", "billing"),
            read_only=True,
            authority="refund.current_state",
            manifest_version="tool-manifest-v1",
            output_schema_version="refund-view-v1",
            preconditions=("authenticated_user", "order_id"),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=(
                "refund_id", "order_id", "status", "amount_minor", "currency",
                "updated_at", "operation_key",
            ),
        ),
        Tool(
            name="refund_eligibility_check",
            description=(
                "按订单当前状态、退款窗口和已有申请检查当前用户的退款资格；"
                "创建退款前必须先读取 eligible 与 order_version"
            ),
            handler=refund_eligibility,
            schema=order_schema,
            allowed_agents=("general", "billing"),
            read_only=True,
            authority="refund.eligibility",
            manifest_version="tool-manifest-v1",
            output_schema_version="refund-eligibility-v1",
            preconditions=("authenticated_user", "order_id"),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=("order_id", "eligible", "reason_code", "amount_minor", "currency", "order_version", "refundable_until"),
        ),
        Tool(
            name="refund_request_create",
            description=(
                "为已通过资格检查的订单提交幂等退款申请；高风险写操作，"
                "必须携带资格结果中的订单版本并等待宿主审批"
            ),
            handler=refund_create,
            schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "订单 ID"},
                    "expected_order_version": {
                        "type": "integer",
                        "description": "refund_eligibility_check 返回的 order_version",
                    },
                    "reason": {"type": "string", "description": "用户提出的退款原因"},
                },
                "required": ["order_id", "expected_order_version", "reason"],
            },
            allowed_agents=("billing",),
            risk=ToolRisk.HIGH,
            read_only=False,
            requires_approval=True,
            timeout_s=5.0,
            authority="refund.request_action",
            manifest_version="tool-manifest-v1",
            output_schema_version="refund-request-result-v1",
            receipt_schema_version="action-receipt-v1",
            preconditions=("authenticated_user", "approved", "fresh_refund_eligibility"),
            idempotency="tool_call_operation_key",
            retry_policy="receipt_reconcile_before_retry",
            typed_outcomes=("COMMITTED", "NOT_COMMITTED", "OUTCOME_UNKNOWN"),
            output_fields=("created", "refund_id", "order_id", "status", "amount_minor", "currency"),
        ),
        Tool(
            name="order_cancel_status",
            description=(
                "按原 operation key 查询当前用户订单取消结果；"
                "仅用于未知写结果对账"
            ),
            handler=order_cancel_status,
            schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "operation_key": {"type": "string"},
                },
                "required": ["order_id", "operation_key"],
            },
            allowed_agents=("general",),
            read_only=True,
            authority="order.cancellation_state",
            manifest_version="tool-manifest-v1",
            output_schema_version="order-cancellation-view-v1",
            preconditions=("authenticated_user", "order_id", "operation_key"),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=(
                "cancellation_id", "order_id", "operation_key", "status",
                "order_version", "created_at",
            ),
        ),
        Tool(
            name="order_cancel",
            description=(
                "取消仍处于已支付状态的订单；必须绑定最新订单版本并获得宿主确认"
            ),
            handler=order_cancel,
            schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "expected_order_version": {"type": "integer"},
                },
                "required": ["order_id", "expected_order_version"],
            },
            allowed_agents=("general",),
            risk=ToolRisk.HIGH,
            read_only=False,
            requires_approval=True,
            timeout_s=5.0,
            authority="order.cancel_action",
            manifest_version="tool-manifest-v1",
            output_schema_version="order-cancel-result-v1",
            receipt_schema_version="action-receipt-v1",
            preconditions=("authenticated_user", "approved", "fresh_order_state"),
            idempotency="tool_call_operation_key",
            retry_policy="receipt_reconcile_before_retry",
            typed_outcomes=("COMMITTED", "NOT_COMMITTED", "OUTCOME_UNKNOWN"),
            output_fields=(
                "created", "cancellation_id", "order_id", "status", "order_version",
            ),
        ),
        Tool(
            name="shipping_address_change_status",
            description="按原 operation key 查询当前用户的地址修改结果",
            handler=shipping_address_change_status,
            schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "new_address": {"type": "string"},
                    "operation_key": {"type": "string"},
                },
                "required": ["order_id", "new_address", "operation_key"],
            },
            allowed_agents=("general",),
            read_only=True,
            authority="order.shipping_address_state",
            manifest_version="tool-manifest-v1",
            output_schema_version="shipping-address-change-view-v1",
            preconditions=("authenticated_user", "order_id", "operation_key"),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=(
                "change_id", "order_id", "new_address", "operation_key",
                "status", "order_version", "created_at",
            ),
        ),
        Tool(
            name="shipping_address_change",
            description=(
                "修改仍处于已支付状态的订单收货地址；"
                "必须绑定最新订单版本并获得宿主确认"
            ),
            handler=shipping_address_change,
            schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "new_address": {"type": "string", "maxLength": 500},
                    "expected_order_version": {"type": "integer"},
                },
                "required": [
                    "order_id", "new_address", "expected_order_version",
                ],
            },
            allowed_agents=("general",),
            risk=ToolRisk.HIGH,
            read_only=False,
            requires_approval=True,
            timeout_s=5.0,
            authority="order.shipping_address_action",
            manifest_version="tool-manifest-v1",
            output_schema_version="shipping-address-change-result-v1",
            receipt_schema_version="action-receipt-v1",
            preconditions=("authenticated_user", "approved", "fresh_order_state"),
            idempotency="tool_call_operation_key",
            retry_policy="receipt_reconcile_before_retry",
            typed_outcomes=("COMMITTED", "NOT_COMMITTED", "OUTCOME_UNKNOWN"),
            output_fields=(
                "created", "change_id", "order_id", "new_address", "status",
                "order_version",
            ),
        ),
        Tool(
            name="account_security_event_list",
            description=(
                "查询当前认证用户最近的登录、凭据或风控安全事件；"
                "用于排查可疑活动，不返回其他用户数据"
            ),
            handler=security_events,
            schema={
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": [item.value for item in SecuritySeverity],
                        "description": "可选安全级别过滤",
                    },
                    "limit": {"type": "integer", "description": "返回数量，1-20"},
                },
            },
            allowed_agents=("account_security",),
            read_only=True,
            authority="account.security_events",
            manifest_version="tool-manifest-v1",
            output_schema_version="security-events-v1",
            preconditions=("authenticated_user",),
            idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("OK", "UNAVAILABLE", "UNAUTHORIZED"),
            output_fields=("event_id", "event_type", "severity", "summary", "occurred_at"),
        ),
    )


def _trusted(context: Optional[Dict[str, Any]], key: str) -> str:
    value = str((context or {}).get(key) or "").strip()
    if not value:
        raise ValueError(f"customer operation tool requires trusted {key} context")
    return value


def _bounded(value: Any, name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return text
