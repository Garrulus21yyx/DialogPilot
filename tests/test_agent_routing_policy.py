"""M2-T01A Agent-owned intent/domain/instance policy registries."""
from dataclasses import FrozenInstanceError, replace

import pytest

from agents.orchestration_contracts import AgentType
from agents.routing_policy import (
    AgentHealthSnapshot,
    AgentRoutingPolicyRegistry,
    InstanceSelectionStatus,
)
from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel


def test_v1_registry_freezes_both_intent_branches_and_has_no_mutable_pointer():
    registry = AgentRoutingPolicyRegistry.v1()
    assert registry.intent_fusion.weights_by_mode["ngram"] == {
        "llm": 0.70, "embedding": 0.20, "pattern": 0.10,
    }
    assert registry.intent_fusion.weights_by_mode["disabled"] == {
        "llm": 0.85, "pattern": 0.15,
    }
    assert registry.intent_fusion.accept_threshold == 0.50
    assert len(registry.fingerprint) == 64
    assert not hasattr(registry, "activate")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        registry.version = "online-feedback-v2"


@pytest.mark.parametrize("mode", ["ngram", "disabled"])
def test_intent_recognizer_fingerprint_consumes_agent_policy(mode):
    policy = AgentRoutingPolicyRegistry.v1().intent_fusion
    recognizer = IntentRecognizer(
        api_key="test", model="test", similarity_mode=mode,
        confidence_threshold=policy.accept_threshold,
        fusion_weights=policy.weights_by_mode,
        fusion_policy_version=policy.version,
    )
    assert recognizer._vote_weights[mode] == dict(policy.weights_by_mode[mode])
    assert len(recognizer.classifier_fingerprint()) == 64


def test_domain_policy_replays_components_hard_rules_and_multi_domain_order():
    policy = AgentRoutingPolicyRegistry.v1().domain_routing
    inputs = dict(
        message="订单 ORD-1 重复扣款并且登录报错 500",
        intent=IntentCategory.PAYMENT_ISSUE,
        urgency=UrgencyLevel.LOW,
        entities={"order_id": ["ORD-1"], "error_code": ["500"]},
        available_owners=(
            AgentType.GENERAL, AgentType.BILLING, AgentType.TECHNICAL,
        ),
    )
    first = policy.decide(**inputs)
    second = policy.decide(**inputs)
    assert first == second
    assert first.ordered_owners[:2] == (
        AgentType.BILLING, AgentType.TECHNICAL,
    )
    assert first.components[AgentType.BILLING]["intent"] == 0.75
    assert first.components[AgentType.TECHNICAL]["entity:error_code"] == 0.20
    handoff = policy.decide(**{
        **inputs, "intent": IntentCategory.HUMAN_HANDOFF,
    })
    assert handoff.ordered_owners == (AgentType.ESCALATION,)
    assert handoff.hard_rule_reason == "EXPLICIT_HUMAN_HANDOFF"


def test_instance_policy_singleton_is_honest_and_multi_instance_is_replayable():
    policy = AgentRoutingPolicyRegistry.v1().instance_selection
    healthy = AgentHealthSnapshot("billing-a", 20, 19, 2000, 10, 0.9, 0.0)
    singleton = policy.select((healthy,))
    assert singleton.status is InstanceSelectionStatus.NOT_APPLICABLE
    assert singleton.selected_instance_id == "billing-a"
    degraded = AgentHealthSnapshot("billing-b", 20, 10, 80000, 10, 0.2, 0.5)
    selected = policy.select((degraded, healthy))
    assert selected.status is InstanceSelectionStatus.SELECTED
    assert selected.selected_instance_id == "billing-a"
    assert selected == policy.select((healthy, degraded))


def test_policy_surfaces_change_independently():
    registry = AgentRoutingPolicyRegistry.v1()
    candidate = replace(
        registry,
        instance_selection=replace(
            registry.instance_selection, version="instance-selection-candidate-v2",
        ),
    )
    assert candidate.intent_fusion.fingerprint == registry.intent_fusion.fingerprint
    assert candidate.domain_routing.fingerprint == registry.domain_routing.fingerprint
    assert candidate.instance_selection.fingerprint != (
        registry.instance_selection.fingerprint
    )
