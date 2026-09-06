import asyncio
from dataclasses import replace
from types import SimpleNamespace

from application.agent_result import AgentResultStatus
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.conversation_agent import ConversationAgent
from application.target_conversation_manager import TargetTurnContext
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
    context = TargetTurnContext()
    context = replace(
        context,
        entity_bindings=EntityBindingResolver().resolve(
            observations, state, context,
        ),
    )
    invocation = (
        understanding.plan(observations, state, deterministic, registry, context)
        if isinstance(understanding, ConversationAgent)
        else understanding(observations, state, deterministic, registry, context)
    )
    proposal = asyncio.run(invocation)
    return proposal, state, registry


def _plan(proposal, state, registry, request_id):
    validated = RoutePolicy().accept(proposal, state, registry)
    identity = IdentityFactory(lambda: request_id).create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
        request_id=request_id,
    )
    return TurnPlanCompiler().compile(validated, state, registry, identity)


class _Provider:
    version = "read-expansion-provider-v1"

    def __init__(self, result):
        self.result = result

    async def plan(self, payload):
        return self.result


def _agent(kind, **goal):
    return ConversationAgent(_Provider({
        "status": "resolved",
        "goals": [{"kind": kind, **goal}],
    }))


def test_refund_eligibility_query_is_direct_read_and_does_not_start_flow():
    proposal, state, registry = _proposal(
        _agent("refund_eligibility", order_id="DP1234"),
        "订单 DP1234 能退款吗？",
    )
    plan = _plan(proposal, state, registry, "eligibility-request")

    assert proposal.commands[0].kind is CommandKind.DIRECT_TOOL
    assert proposal.commands[0].tool_id == "refund_eligibility_check"
    assert plan.route.mode is RouteMode.DIRECT
    assert plan.transitions is None
    assert plan.work.items[0].effect.value == "READ"


def test_explicit_refund_execution_still_uses_governed_flow_preparation():
    proposal, state, registry = _proposal(
        _agent("execute_refund", order_id="DP1234"),
        "把订单 DP1234 退掉",
    )
    plan = _plan(proposal, state, registry, "refund-write-request")

    assert proposal.commands[0].kind is CommandKind.PREPARE_WORKFLOW
    assert plan.transitions is not None
    assert plan.transitions.mutations[0].flow_ref == "execute_refund:v1"


def test_order_cancellation_uses_registry_preparation_and_not_status_read_path():
    proposal, state, registry = _proposal(
        _agent("cancel_order", order_id="DP1234"), "取消订单 DP1234",
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


def test_refund_policy_and_invoice_use_the_same_atomic_knowledge_tool():
    refund, state, registry = _proposal(
        _agent("refund_policy"), "退款政策和一般时效是什么？",
    )
    invoice, _, _ = _proposal(
        _agent("invoice_qa"), "电子发票怎么开？",
    )

    assert refund.commands[0].tool_id == "knowledge_search"
    assert invoice.commands[0].tool_id == "knowledge_search"
    assert refund.commands[0].skill_id is None
    assert invoice.commands[0].skill_id is None
    assert tuple(skill.skill_id for skill in registry.skills) == (
        "product_identification",
    )
    assert (
        _plan(refund, state, registry, "refund-policy").route.mode
        is RouteMode.KNOWLEDGE_QA
    )


def test_structured_logistics_goal_reuses_direct_order_authority():
    router = ConversationAgent(_Provider({
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
    router = ConversationAgent(_Provider({
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
    router = ConversationAgent(_Provider({
        "status": "resolved",
        "goals": [{
            "kind": "refund_eligibility",
            "order_id": "DP9999",
        }],
    }))
    proposal, _, _ = _proposal(router, "这个能退吗？")

    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


def test_product_qa_uses_one_atomic_knowledge_tool_across_product_categories():
    registry = build_default_capability_registry("tenant-a")
    product_agent = registry.agent("product_technical")

    assert product_agent.allowed_skill_ids == ("product_identification",)

    for index, question in enumerate((
        "这款机械键盘支持 Mac 吗？",
        "这件外套应该怎么选尺码？",
        "这台摄像机能接入家庭网络吗？",
    )):
        router = ConversationAgent(_Provider({
            "status": "resolved",
            "goals": [{"kind": "product_qa"}],
        }))
        proposal, state, current_registry = _proposal(router, question)
        plan = _plan(
            proposal, state, current_registry, f"generic-product-{index}",
        )

        assert proposal.commands[0].skill_id is None
        assert proposal.commands[0].tool_id == "knowledge_search"
        assert dict(
            (item.name, item.value) for item in proposal.commands[0].arguments
        ) == {"query": question}
        assert plan.route.mode is RouteMode.KNOWLEDGE_QA
        assert plan.work.items[0].allowed_tools == ("knowledge_search",)


def test_generic_product_question_is_not_recast_as_missing_media():
    proposal, _, _ = _proposal(
        _agent("product_qa"), "这个商品支持 Mac 吗？",
    )

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert proposal.commands[0].tool_id == "knowledge_search"


def test_generic_product_qa_executes_the_shared_knowledge_tool_contract():
    question = "这款机械键盘支持 Mac 吗？"
    router = ConversationAgent(_Provider({
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
                tool_name=name,
                data=__import__("tests.test_knowledge_tool_contract", fromlist=["evidence_result"]).evidence_result("支持 macOS。"),
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
        {"query": question},
        "technical",
    )
