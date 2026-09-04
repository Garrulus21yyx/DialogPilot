import asyncio
from types import SimpleNamespace

from application.agent_result import AgentResultStatus
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.structured_target_router import StructuredTargetCommandRouter
from application.target_understanding import BoundedTargetUnderstanding
from application.orchestration_runtime import AgentContextView
from application.turn_planning import (
    CommandKind,
    ProposalDisposition,
    RouteMode,
    RoutePolicy,
    TurnPlanCompiler,
)
from core.identity import IdentityFactory
from infrastructure.target_tool_execution import TargetToolExecutor


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


def test_order_cancellation_uses_registry_preparation_and_not_status_read_path():
    proposal, state, registry = _proposal(
        BoundedTargetUnderstanding(), "取消订单 DP1234",
    )
    plan = _plan(proposal, state, registry, "cancel-order-request")

    command = proposal.commands[0]
    item = plan.work.items[0]
    mutation = plan.transitions.mutations[0]
    assert command.kind is CommandKind.PREPARE_WORKFLOW
    assert command.action_ref == "order.cancel:v1"
    assert item.allowed_tools == ("order_lookup",)
    assert mutation.flow_ref == "cancel_order:v1"
    assert mutation.readiness_field == "status"
    assert mutation.readiness_value_json == '"paid"'
    assert mutation.target_version_argument == "expected_order_version"


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


def test_structured_order_cancellation_uses_the_same_governed_flow_contract():
    router = StructuredTargetCommandRouter(_Provider({
        "status": "resolved",
        "goals": [{
            "kind": "cancel_order",
            "order_id": "DP1234",
        }],
    }))
    proposal, state, registry = _proposal(router, "取消 DP1234 这个订单")
    plan = _plan(proposal, state, registry, "semantic-order-cancel")

    assert proposal.commands[0].action_ref == "order.cancel:v1"
    assert plan.work.items[0].allowed_tools == ("order_lookup",)
    assert plan.transitions.mutations[0].flow_ref == "cancel_order:v1"


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


def test_product_qa_is_one_task_skill_across_unrelated_product_categories():
    registry = build_default_capability_registry("tenant-a")
    product_agent = registry.agent("product_technical")

    assert product_agent.allowed_skill_ids == (
        "product_identification", "product_qa",
    )
    assert registry.skill("product_qa").required_arguments == ("question",)
    assert registry.skill("product_qa").allowed_tool_ids == ("knowledge_search",)

    for index, question in enumerate((
        "这款机械键盘支持 Mac 吗？",
        "这件外套应该怎么选尺码？",
        "这台摄像机能接入家庭网络吗？",
    )):
        router = StructuredTargetCommandRouter(_Provider({
            "status": "resolved",
            "goals": [{"kind": "product_qa"}],
        }))
        proposal, state, current_registry = _proposal(router, question)
        plan = _plan(
            proposal, state, current_registry, f"generic-product-{index}",
        )

        assert proposal.commands[0].skill_id == "product_qa"
        assert dict(
            (item.name, item.value) for item in proposal.commands[0].arguments
        ) == {"question": question}
        assert plan.route.mode is RouteMode.AGENT_TASK
        assert plan.work.items[0].allowed_tools == ("knowledge_search",)


def test_generic_product_question_is_not_recast_as_missing_media():
    proposal, _, _ = _proposal(
        BoundedTargetUnderstanding(), "这个商品支持 Mac 吗？",
    )

    assert proposal.disposition is ProposalDisposition.CLARIFY
    assert proposal.reason_code == "SUPPORTED_GOAL_UNCLEAR"
    assert proposal.missing_inputs == ("customer_service_goal",)


def test_generic_product_qa_executes_the_shared_knowledge_tool_contract():
    question = "这款机械键盘支持 Mac 吗？"
    router = StructuredTargetCommandRouter(_Provider({
        "status": "resolved",
        "goals": [{"kind": "product_qa"}],
    }))
    proposal, state, registry = _proposal(router, question)
    item = _plan(proposal, state, registry, "product-runtime").work.items[0]

    class KnowledgeTools:
        def __init__(self):
            self.calls = []

        async def execute_for_agent(
            self, name, params, *, agent_type, context, call_id,
        ):
            self.calls.append((name, params, agent_type, context, call_id))
            return SimpleNamespace(
                success=True,
                status="success",
                authority="knowledge.active_source",
                data={"status": "OK", "answer": "支持 macOS。"},
                receipt_id="evidence:product-qa-1",
                call_id=call_id,
                output_schema_version="knowledge-evidence-pack-result-v1",
                output_for_model="支持 macOS。",
            )

    tools = KnowledgeTools()
    result = asyncio.run(TargetToolExecutor(tools)(AgentContextView(
        item, question, (), (), (), 2000,
        {"tenant_id": "tenant-a", "user_id": "user-a", "conversation_id": "conversation-a"},
    )))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.facts[0].requirement_id == "knowledge.active_source"
    assert result.candidate_response == "支持 macOS。"
    assert tools.calls[0][0:3] == (
        "knowledge_search",
        {"question": question, "query": question},
        "technical",
    )
