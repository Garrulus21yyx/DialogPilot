"""M2-T01 RouteDecision and RouterInvocationPolicy call/skip algebra."""
from types import MappingProxyType

import pytest

from agents.orchestration_contracts import AgentType
from agents.routing_policy import (
    DomainDecision,
    DomainRoutingPolicy,
    InstanceSelectionDecision,
    InstanceSelectionStatus,
)
from application.route_decision import (
    ComponentStatus,
    ContinuationMode,
    IntentRouteSignal,
    RequestShape,
    RequiredAuthority,
    RouteMode,
    RouteRisk,
    RouterInvocation,
    RouterInvocationPolicy,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel


FP = "a" * 64


class Calls:
    def __init__(self):
        self.intent = 0
        self.domain = 0
        self.instance = 0
        self.signal = IntentRouteSignal(
            intent="refund", confidence=0.9,
            request_shape=RequestShape.BUSINESS_STATE,
            required_authorities=(RequiredAuthority.REFUND_STATE,),
            risk=RouteRisk.MEDIUM, reason_codes=("INTENT_REFUND",),
            input_fingerprint=FP,
        )

    def intent_port(self):
        self.intent += 1
        return self.signal

    def domain_port(self):
        self.domain += 1
        return DomainDecision(
            ordered_owners=(AgentType.BILLING,),
            scores=MappingProxyType({AgentType.BILLING: 1.2}),
            components=MappingProxyType({
                AgentType.BILLING: MappingProxyType({"intent": 0.75}),
            }),
            hard_rule_reason=None,
            policy_version="domain-routing-v1",
            policy_fingerprint=FP,
            input_fingerprint=FP,
        )

    def instance_port(self, owner):
        self.instance += 1
        assert owner == "billing"
        return InstanceSelectionDecision(
            InstanceSelectionStatus.SELECTED, "billing-1",
            MappingProxyType({"billing-0": 0.7, "billing-1": 0.8}),
            FP, "HIGHEST_FROZEN_HEALTH_SCORE",
            "instance-selection-v1", FP,
        )


def _invocation(calls, **changes):
    values = dict(
        input_fingerprint=FP,
        request_shape=RequestShape.UNKNOWN,
        intent_port=calls.intent_port,
        domain_port=calls.domain_port,
        instance_port=calls.instance_port,
        owner_pool_sizes={"billing": 1},
    )
    values.update(changes)
    return RouterInvocation(**values)


@pytest.mark.parametrize("shape,mode", [
    (RequestShape.GREETING, RouteMode.DIRECT),
    (RequestShape.THANKS, RouteMode.DIRECT),
    (RequestShape.KNOWLEDGE_FAQ, RouteMode.KNOWLEDGE_QA),
    (RequestShape.MISSING_INPUT, RouteMode.CLARIFY),
    (RequestShape.EXPLICIT_HANDOFF, RouteMode.HANDOFF),
    (RequestShape.OUT_OF_SCOPE, RouteMode.OUT_OF_SCOPE),
    (RequestShape.UNSUPPORTED_OPERATION, RouteMode.HANDOFF),
])
def test_deterministic_shapes_forbid_all_expensive_router_calls(shape, mode):
    calls = Calls()
    decision = RouterInvocationPolicy().decide(_invocation(
        calls, request_shape=shape,
    ))
    assert decision.mode is mode
    assert (calls.intent, calls.domain, calls.instance) == (0, 0, 0)
    assert [item.status for item in decision.component_invocations] == [
        ComponentStatus.SKIPPED,
        ComponentStatus.SKIPPED,
        ComponentStatus.SKIPPED,
    ]


def test_personal_state_skips_intent_but_requires_domain_and_not_knowledge_answer():
    calls = Calls()
    decision = RouterInvocationPolicy().decide(_invocation(
        calls, request_shape=RequestShape.BUSINESS_STATE,
    ))
    assert decision.mode is RouteMode.AGENT_TASK
    assert decision.required_authorities == (RequiredAuthority.DOMAIN_TOOL,)
    assert (calls.intent, calls.domain, calls.instance) == (0, 1, 0)
    assert decision.component_invocations[2].status is ComponentStatus.NOT_APPLICABLE


def test_domain_decision_separates_ranked_candidates_from_selected_owners():
    policy = DomainRoutingPolicy("domain-routing-v1")
    decision = policy.decide(
        message="我的退款状态", intent=IntentCategory.REFUND,
        urgency=UrgencyLevel.LOW, entities={},
        available_owners=(
            AgentType.GENERAL, AgentType.TECHNICAL, AgentType.BILLING,
            AgentType.ACCOUNT_SECURITY,
        ),
    )

    assert len(decision.ordered_owners) == 4
    assert decision.selected_owners == (AgentType.BILLING,)


def test_router_consumes_selected_owners_not_every_ranked_candidate():
    calls = Calls()
    ranked = calls.domain_port()
    calls.domain_port = lambda: DomainDecision(
        ordered_owners=(
            AgentType.BILLING, AgentType.GENERAL, AgentType.TECHNICAL,
        ),
        scores=ranked.scores, components=ranked.components,
        hard_rule_reason=None, policy_version=ranked.policy_version,
        policy_fingerprint=ranked.policy_fingerprint,
        input_fingerprint=ranked.input_fingerprint,
        selected_owners=(AgentType.BILLING,),
    )

    decision = RouterInvocationPolicy().decide(_invocation(
        calls, request_shape=RequestShape.BUSINESS_STATE,
    ))

    assert decision.owner_ids == ("billing",)


def test_unknown_and_switch_invoke_one_intent_then_only_worker_components_needed():
    calls = Calls()
    decision = RouterInvocationPolicy().decide(_invocation(calls))
    assert decision.mode is RouteMode.AGENT_TASK
    assert (calls.intent, calls.domain, calls.instance) == (1, 1, 0)
    assert [item.status for item in decision.component_invocations] == [
        ComponentStatus.INVOKED,
        ComponentStatus.INVOKED,
        ComponentStatus.NOT_APPLICABLE,
    ]
    switched = Calls()
    RouterInvocationPolicy().decide(_invocation(
        switched, continuation_mode=ContinuationMode.SWITCH,
    ))
    assert switched.intent == 1


def test_multi_instance_worker_route_invokes_instance_once():
    calls = Calls()
    decision = RouterInvocationPolicy().decide(_invocation(
        calls, owner_pool_sizes={"billing": 2},
    ))
    assert calls.instance == 1
    assert decision.component_invocations[2].status is ComponentStatus.INVOKED


def test_continue_and_pending_signal_reuse_pinned_route_without_any_router_call():
    for pending, continuation, reason in (
        (True, None, "PENDING_SIGNAL_RESUME"),
        (False, ContinuationMode.CONTINUE, "CONTINUE_PINNED_FRAME"),
    ):
        calls = Calls()
        decision = RouterInvocationPolicy().decide(_invocation(
            calls,
            pending_signal_reply=pending,
            continuation_mode=continuation,
            prior_route_mode=RouteMode.AGENT_TASK,
            prior_intent="refund",
            prior_authorities=(RequiredAuthority.REFUND_STATE,),
        ))
        assert decision.reason_codes == (reason,)
        assert (calls.intent, calls.domain, calls.instance) == (0, 0, 0)


def test_mixed_question_declares_both_authorities_and_human_rule_beats_rag():
    calls = Calls()
    mixed = RouterInvocationPolicy().decide(_invocation(
        calls, request_shape=RequestShape.MIXED_POLICY_STATE,
    ))
    assert mixed.mode is RouteMode.MIXED
    assert mixed.required_authorities == (
        RequiredAuthority.KNOWLEDGE, RequiredAuthority.DOMAIN_TOOL,
    )
    handoff = RouterInvocationPolicy().decide(_invocation(
        Calls(), request_shape=RequestShape.EXPLICIT_HANDOFF,
    ))
    assert handoff.mode is RouteMode.HANDOFF
    assert RequiredAuthority.KNOWLEDGE not in handoff.required_authorities


def test_security_hard_rule_cannot_be_overridden_by_soft_intent_signal():
    calls = Calls()
    calls.signal = IntentRouteSignal(
        intent="greeting", confidence=1.0,
        request_shape=RequestShape.GREETING, required_authorities=(),
        risk=RouteRisk.LOW,
    )
    decision = RouterInvocationPolicy().decide(_invocation(
        calls, request_shape=RequestShape.SECURITY_RISK,
    ))
    assert decision.mode is RouteMode.AGENT_TASK
    assert decision.risk is RouteRisk.CRITICAL
    assert RequiredAuthority.SECURITY in decision.required_authorities
    assert calls.intent == 0


def test_emotion_is_auxiliary_and_cannot_force_handoff_or_change_authority():
    baseline = RouterInvocationPolicy().decide(_invocation(
        Calls(), request_shape=RequestShape.KNOWLEDGE_FAQ,
    ))
    emotional = RouterInvocationPolicy().decide(_invocation(
        Calls(), request_shape=RequestShape.KNOWLEDGE_FAQ,
        emotion_signal="very_angry",
    ))
    assert emotional.mode is baseline.mode is RouteMode.KNOWLEDGE_QA
    assert emotional.required_authorities == baseline.required_authorities
    assert emotional.auxiliary_signals == ("emotion:very_angry",)


def test_missing_intent_or_domain_port_fails_closed_without_second_router():
    calls = Calls()
    clarify = RouterInvocationPolicy().decide(_invocation(
        calls, intent_port=None,
    ))
    assert clarify.mode is RouteMode.CLARIFY
    calls = Calls()
    handoff = RouterInvocationPolicy().decide(_invocation(
        calls, domain_port=None,
    ))
    assert handoff.mode is RouteMode.HANDOFF
    assert RequiredAuthority.HUMAN in handoff.required_authorities


@pytest.mark.parametrize("mode,legacy", [
    (RouteMode.CLARIFY, "clarify"),
    (RouteMode.OUT_OF_SCOPE, "out_of_scope"),
    (RouteMode.DIRECT, "execute"),
    (RouteMode.KNOWLEDGE_QA, "execute"),
    (RouteMode.HANDOFF, "execute"),
])
def test_legacy_planner_disposition_is_only_a_compatibility_projection(mode, legacy):
    calls = Calls()
    shape = {
        RouteMode.CLARIFY: RequestShape.MISSING_INPUT,
        RouteMode.OUT_OF_SCOPE: RequestShape.OUT_OF_SCOPE,
        RouteMode.DIRECT: RequestShape.GREETING,
        RouteMode.KNOWLEDGE_QA: RequestShape.KNOWLEDGE_FAQ,
        RouteMode.HANDOFF: RequestShape.EXPLICIT_HANDOFF,
    }[mode]
    decision = RouterInvocationPolicy().decide(_invocation(
        calls, request_shape=shape,
    ))
    assert decision.legacy_planning_disposition == legacy
