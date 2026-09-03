import asyncio

from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.structured_target_router import StructuredTargetCommandRouter
from application.target_understanding import BoundedTargetUnderstanding
from application.turn_planning import (
    CommandKind,
    ProposalDisposition,
    RouteMode,
    RoutePolicy,
    TurnPlanCompiler,
)
from core.identity import IdentityFactory


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


def _proposal(understanding, message):
    state = _state()
    observations = TurnObservations(message)
    deterministic = DeterministicResolver().resolve(observations, state)
    registry = build_default_capability_registry("tenant-a")
    proposal = asyncio.run(understanding(
        observations, state, deterministic, registry,
    ))
    return proposal, state, registry


def _plan(proposal, state, registry, request_id):
    validated = RoutePolicy().accept(proposal, state, registry)
    identity = IdentityFactory(lambda: request_id).create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
        request_id=request_id,
    )
    return TurnPlanCompiler().compile(validated, state, registry, identity)


def test_refund_eligibility_query_is_direct_read_and_does_not_start_flow():
    proposal, state, registry = _proposal(
        BoundedTargetUnderstanding(), "订单 DP1234 能退款吗？",
    )
    plan = _plan(proposal, state, registry, "eligibility-request")

    assert proposal.commands[0].kind is CommandKind.DIRECT_TOOL
    assert proposal.commands[0].tool_id == "refund_eligibility_check"
    assert plan.route.mode is RouteMode.DIRECT
    assert plan.transitions is None
    assert plan.work.items[0].effect.value == "READ"


def test_explicit_refund_execution_still_uses_governed_flow_preparation():
    proposal, state, registry = _proposal(
        BoundedTargetUnderstanding(), "把订单 DP1234 退掉",
    )
    plan = _plan(proposal, state, registry, "refund-write-request")

    assert proposal.commands[0].kind is CommandKind.PREPARE_WORKFLOW
    assert plan.transitions is not None
    assert plan.transitions.mutations[0].flow_ref == "execute_refund:v1"


def test_refund_policy_and_invoice_are_separate_registered_knowledge_skills():
    refund, state, registry = _proposal(
        BoundedTargetUnderstanding(), "退款政策和一般时效是什么？",
    )
    invoice, _, _ = _proposal(
        BoundedTargetUnderstanding(), "电子发票怎么开？",
    )

    assert refund.commands[0].skill_id == "refund_policy_qa"
    assert invoice.commands[0].skill_id == "invoice_qa"
    assert registry.skill("refund_policy_qa").owner_agent == "billing_refund"
    assert registry.skill("invoice_qa").allowed_tool_ids == ("knowledge_search",)
    assert _plan(refund, state, registry, "refund-policy").route.mode is RouteMode.AGENT_TASK


class _Provider:
    version = "read-expansion-provider-v1"

    def __init__(self, result):
        self.result = result

    async def route(self, payload):
        return self.result


def test_structured_logistics_goal_reuses_direct_order_authority():
    router = StructuredTargetCommandRouter(_Provider({
        "status": "resolved",
        "goals": [{
            "kind": "logistics_status",
            "order_id": "DP1234",
        }],
    }))
    proposal, state, registry = _proposal(router, "DP1234 到哪一站了？")
    plan = _plan(proposal, state, registry, "logistics-request")

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert proposal.commands[0].tool_id == "order_lookup"
    assert plan.route.mode is RouteMode.DIRECT


def test_structured_refund_eligibility_cannot_invent_missing_order_id():
    router = StructuredTargetCommandRouter(_Provider({
        "status": "resolved",
        "goals": [{
            "kind": "refund_eligibility",
            "order_id": "DP9999",
        }],
    }))
    proposal, _, _ = _proposal(router, "这个能退吗？")

    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT
