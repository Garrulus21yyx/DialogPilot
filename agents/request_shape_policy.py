"""Agent-owned deterministic request-shape and authority policy."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Sequence

from application.route_decision import (
    RequestShape,
    RequiredAuthority,
    RouteRisk,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel


@dataclass(frozen=True)
class RequestShapeDecision:
    shape: RequestShape
    required_authorities: tuple[RequiredAuthority, ...]
    risk: RouteRisk
    reason_codes: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    policy_version: str
    input_fingerprint: str


class RequestShapePolicy:
    """Normalize hard facts before RouterInvocationPolicy; never calls a model."""

    version = "request-shape-policy-v1"
    confidence_threshold = 0.50

    def decide(
        self,
        *,
        message: str,
        intent: IntentCategory,
        confidence: float,
        urgency: UrgencyLevel,
        entities: Mapping[str, Sequence[str]],
    ) -> RequestShapeDecision:
        normalized = str(message or "").strip().casefold()
        fingerprint = hashlib.sha256(json.dumps({
            "message": normalized,
            "intent": intent.value,
            "confidence": round(float(confidence), 8),
            "urgency": urgency.name,
            "entities": {key: list(value) for key, value in sorted(entities.items())},
            "policy_version": self.version,
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

        def result(shape, authorities, risk, reason, missing=()):
            return RequestShapeDecision(
                shape, tuple(authorities), risk, (reason,), tuple(missing),
                self.version, fingerprint,
            )

        if intent is IntentCategory.GREETING:
            return result(RequestShape.GREETING, (), RouteRisk.LOW, "GREETING_INTENT")
        if intent is IntentCategory.FEEDBACK:
            return result(RequestShape.THANKS, (), RouteRisk.LOW, "THANKS_OR_FEEDBACK")
        if intent in {IntentCategory.HUMAN_HANDOFF, IntentCategory.ESCALATION}:
            return result(
                RequestShape.EXPLICIT_HANDOFF, (RequiredAuthority.HUMAN,),
                RouteRisk.HIGH, "EXPLICIT_HANDOFF_INTENT",
            )
        if urgency is UrgencyLevel.CRITICAL:
            return result(
                RequestShape.EXPLICIT_HANDOFF, (RequiredAuthority.HUMAN,),
                RouteRisk.CRITICAL, "CRITICAL_URGENCY_HANDOFF",
            )
        if intent is IntentCategory.OTHER:
            if confidence < self.confidence_threshold:
                return result(
                    RequestShape.MISSING_INPUT, (), RouteRisk.MEDIUM,
                    "LOW_CONFIDENCE_GOAL", ("request_goal",),
                )
            return result(
                RequestShape.OUT_OF_SCOPE, (), RouteRisk.LOW,
                "CONFIDENT_OUT_OF_SCOPE",
            )
        if intent is IntentCategory.ACCOUNT_SECURITY:
            return result(
                RequestShape.SECURITY_RISK,
                (RequiredAuthority.SECURITY, RequiredAuthority.DOMAIN_TOOL),
                RouteRisk.CRITICAL, "SECURITY_HARD_RULE",
            )

        domain_count = self._domain_count(normalized, intent)
        if domain_count > 1:
            return result(
                RequestShape.MULTI_DOMAIN_TASK, (RequiredAuthority.DOMAIN_TOOL,),
                RouteRisk.MEDIUM, "MULTIPLE_INDEPENDENT_DOMAINS",
            )

        action = self._has(normalized, (
            "帮我退款", "申请退款", "我要退款", "立即退款", "现在退款",
        ))
        if action:
            return result(
                RequestShape.ACTION_REQUEST,
                (RequiredAuthority.ACTION_APPROVAL,), RouteRisk.HIGH,
                "EXPLICIT_WRITE_ACTION",
            )

        state = bool(any(entities.get(key) for key in ("order_id", "amount"))) or self._has(
            normalized,
            ("我的", "这笔", "当前", "现在", "状态", "进度", "到哪", "为什么扣", "重复扣"),
        )
        policy = self._has(
            normalized,
            ("政策", "规则", "条件", "时限", "一般", "通常", "是否支持", "流程", "多久到账"),
        )
        if state and policy:
            return result(
                RequestShape.MIXED_POLICY_STATE,
                (RequiredAuthority.KNOWLEDGE, RequiredAuthority.DOMAIN_TOOL),
                RouteRisk.MEDIUM, "PERSONAL_STATE_WITH_POLICY",
            )
        if state and intent in {
            IntentCategory.ORDER_STATUS, IntentCategory.LOGISTICS,
            IntentCategory.REFUND, IntentCategory.PAYMENT_ISSUE,
            IntentCategory.BILLING, IntentCategory.ACCOUNT,
        }:
            return result(
                RequestShape.BUSINESS_STATE, (RequiredAuthority.DOMAIN_TOOL,),
                RouteRisk.MEDIUM, "PERSONAL_STATE_REQUIRES_TOOL",
            )
        if policy or intent in {
            IntentCategory.QUERY, IntentCategory.INVOICE, IntentCategory.LOGISTICS,
        }:
            return result(
                RequestShape.KNOWLEDGE_FAQ, (RequiredAuthority.KNOWLEDGE,),
                RouteRisk.LOW, "STATIC_KNOWLEDGE_REQUEST",
            )
        return result(
            RequestShape.AGENT_ASSISTANCE, (RequiredAuthority.KNOWLEDGE,),
            RouteRisk.MEDIUM, "BOUNDED_AGENT_ASSISTANCE",
        )

    @classmethod
    def _domain_count(cls, message: str, intent: IntentCategory) -> int:
        groups = {
            "technical": (
                intent in {IntentCategory.TECHNICAL, IntentCategory.TECHNICAL_LOGIN,
                           IntentCategory.TECHNICAL_CRASH}
                or cls._has(message, ("登录", "报错", "错误", "崩溃", "error", "crash"))
            ),
            "billing": (
                intent in {IntentCategory.BILLING, IntentCategory.REFUND,
                           IntentCategory.INVOICE, IntentCategory.PAYMENT_ISSUE}
                or cls._has(message, ("退款", "扣款", "发票", "账单", "支付"))
            ),
            "security": cls._has(message, ("被盗", "异常登录", "陌生设备", "密码泄露")),
            "general": (
                intent in {IntentCategory.ORDER_STATUS, IntentCategory.LOGISTICS,
                           IntentCategory.ACCOUNT}
                or cls._has(message, ("订单", "物流", "配送", "账户资料"))
            ),
        }
        return sum(groups.values())

    @staticmethod
    def _has(message: str, terms: Sequence[str]) -> bool:
        negators = ("不是", "并非", "没有", "不涉及", "无关")
        for term in terms:
            index = message.find(term)
            if index >= 0 and not any(
                negator in message[max(0, index - 8):index] for negator in negators
            ):
                return True
        return False
