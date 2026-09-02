"""Agent-owned RequestShapePolicy hard-rule and authority invariants."""
import pytest

from agents.request_shape_policy import RequestShapePolicy
from application.route_decision import (
    RequestShape,
    RequiredAuthority,
    RouteRisk,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel


@pytest.mark.parametrize(
    ("message", "intent", "confidence", "entities", "shape"),
    [
        ("你好", IntentCategory.GREETING, 1.0, {}, RequestShape.GREETING),
        ("谢谢", IntentCategory.FEEDBACK, 1.0, {}, RequestShape.THANKS),
        ("我要人工", IntentCategory.HUMAN_HANDOFF, 1.0, {}, RequestShape.EXPLICIT_HANDOFF),
        ("股票走势", IntentCategory.OTHER, 0.9, {}, RequestShape.OUT_OF_SCOPE),
        ("这个怎么办", IntentCategory.OTHER, 0.3, {}, RequestShape.MISSING_INPUT),
        ("账号被盗", IntentCategory.ACCOUNT_SECURITY, 1.0, {}, RequestShape.SECURITY_RISK),
        ("登录失败而且重复扣款", IntentCategory.TECHNICAL_LOGIN, 0.9, {}, RequestShape.MULTI_DOMAIN_TASK),
        ("我的退款进度，一般多久到账", IntentCategory.REFUND, 0.9, {}, RequestShape.MIXED_POLICY_STATE),
        ("我的订单状态", IntentCategory.ORDER_STATUS, 0.9, {"order_id": ["A1"]}, RequestShape.BUSINESS_STATE),
        ("帮我申请退款", IntentCategory.REFUND, 0.9, {}, RequestShape.ACTION_REQUEST),
        ("退款政策和时限", IntentCategory.REFUND, 0.9, {}, RequestShape.KNOWLEDGE_FAQ),
        ("应用崩溃", IntentCategory.TECHNICAL_CRASH, 0.9, {}, RequestShape.AGENT_ASSISTANCE),
    ],
)
def test_request_shape_policy_closes_supported_shapes(
    message, intent, confidence, entities, shape,
):
    decision = RequestShapePolicy().decide(
        message=message, intent=intent, confidence=confidence,
        urgency=UrgencyLevel.LOW, entities=entities,
    )

    assert decision.shape is shape
    assert decision.reason_codes
    assert len(decision.input_fingerprint) == 64


def test_action_and_mixed_authorities_are_not_reduced_to_knowledge():
    action = RequestShapePolicy().decide(
        message="立即申请退款", intent=IntentCategory.REFUND, confidence=0.9,
        urgency=UrgencyLevel.LOW, entities={},
    )
    mixed = RequestShapePolicy().decide(
        message="我的退款进度和一般时限", intent=IntentCategory.REFUND,
        confidence=0.9, urgency=UrgencyLevel.LOW, entities={},
    )

    assert action.required_authorities == (RequiredAuthority.ACTION_APPROVAL,)
    assert action.risk is RouteRisk.HIGH
    assert mixed.required_authorities == (
        RequiredAuthority.KNOWLEDGE, RequiredAuthority.DOMAIN_TOOL,
    )


def test_negated_secondary_domain_does_not_create_multi_domain_shape():
    decision = RequestShapePolicy().decide(
        message="只是登录失败，不涉及退款", intent=IntentCategory.TECHNICAL_LOGIN,
        confidence=0.9, urgency=UrgencyLevel.LOW, entities={},
    )

    assert decision.shape is RequestShape.AGENT_ASSISTANCE
