import asyncio
from types import SimpleNamespace

from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.structured_target_router import (
    CascadedTargetUnderstanding,
    StructuredTargetCommandRouter,
)
from application.target_understanding import BoundedTargetUnderstanding
from application.turn_planning import (
    CommandKind,
    ProposalDisposition,
    RouteMode,
    RoutePolicy,
    TurnPlanCompiler,
)
from core.identity import IdentityFactory
from infrastructure.target_semantic_provider import AnthropicTargetSemanticProvider


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


def _invoke(router, text, *, fields=()):
    state = _state()
    observations = TurnObservations(text, fields)
    deterministic = DeterministicResolver().resolve(observations, state)
    registry = build_default_capability_registry("tenant-a")
    proposal = asyncio.run(router(observations, state, deterministic, registry))
    return proposal, state, registry


class Provider:
    version = "provider-test-v1"

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    async def route(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.value


def test_semantic_router_compiles_only_observed_entities_into_registry_command():
    provider = Provider({
        "status": "resolved",
        "goals": [{"goal_id": "where", "kind": "order_status", "order_id": "DP1234"}],
    })
    proposal, state, registry = _invoke(
        StructuredTargetCommandRouter(provider), "帮我看看 DP1234 走到哪一步了",
    )

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert proposal.commands[0].kind is CommandKind.DIRECT_TOOL
    assert proposal.commands[0].tool_id == "order_lookup"
    validated = RoutePolicy().accept(proposal, state, registry)
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
        request_id="semantic-request-1",
    )
    plan = TurnPlanCompiler().compile(validated, state, registry, identity)
    assert plan.route.mode is RouteMode.DIRECT


def test_semantic_router_rejects_hallucinated_entity_and_unknown_goal():
    invented = Provider({
        "status": "resolved",
        "goals": [{"kind": "refund_status", "order_id": "DP9999"}],
    })
    proposal, _, _ = _invoke(
        StructuredTargetCommandRouter(invented), "查询 DP1234",
    )
    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT

    unknown = Provider({
        "status": "resolved", "goals": [{"kind": "delete_account"}],
    })
    proposal, _, _ = _invoke(StructuredTargetCommandRouter(unknown), "删除账户")
    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


def test_address_change_compiles_one_governed_flow_and_rejects_invented_address():
    text = "把订单 DP1234 的地址改成 Berlin Example Street 9"
    provider = Provider({
        "status": "resolved",
        "goals": [{
            "kind": "change_address",
            "order_id": "DP1234",
            "new_address": "Berlin Example Street 9",
        }],
    })
    proposal, state, registry = _invoke(
        StructuredTargetCommandRouter(provider), text,
    )

    assert proposal.disposition is ProposalDisposition.RESOLVED
    command = proposal.commands[0]
    assert command.kind is CommandKind.PREPARE_WORKFLOW
    assert command.action_ref == "order.shipping_address.change:v1"
    plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(proposal, state, registry),
        state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            request_id="address-request",
        ),
    )
    assert plan.transitions.mutations[0].flow_ref == "change_shipping_address:v1"
    assert plan.work.items[0].allowed_tools == ("order_lookup",)

    invented = Provider({
        "status": "resolved",
        "goals": [{
            "kind": "change_address",
            "order_id": "DP1234",
            "new_address": "Invented Address 1",
        }],
    })
    rejected, _, _ = _invoke(StructuredTargetCommandRouter(invented), text)
    assert rejected.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


def test_bounded_address_change_uses_task_flow_not_a_product_or_address_skill():
    proposal, _, registry = _invoke(
        BoundedTargetUnderstanding(),
        "把订单 DP1234 的地址改成 Berlin Example Street 9",
    )

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert proposal.commands[0].kind is CommandKind.PREPARE_WORKFLOW
    assert proposal.commands[0].skill_id is None
    assert proposal.commands[0].action_ref == "order.shipping_address.change:v1"
    assert all("address" not in skill.skill_id for skill in registry.skills)

    missing, _, _ = _invoke(
        BoundedTargetUnderstanding(),
        "订单 DP1234 修改地址",
    )
    assert missing.disposition is ProposalDisposition.CLARIFY
    assert missing.reason_code == "NEW_ADDRESS_REQUIRED"

def test_semantic_router_preserves_typed_rejection_and_provider_failure():
    insufficient = Provider({
        "status": "insufficient_context", "missing_fields": ["customer_service_goal"],
    })
    proposal, _, _ = _invoke(
        StructuredTargetCommandRouter(insufficient), "还是那个问题",
    )
    assert proposal.disposition is ProposalDisposition.CLARIFY
    assert proposal.missing_inputs == ("customer_service_goal",)

    failed = Provider(error=TimeoutError("provider timeout"))
    proposal, _, _ = _invoke(StructuredTargetCommandRouter(failed), "帮我处理一下")
    assert proposal.disposition is ProposalDisposition.PROVIDER_FAILURE


def test_cascade_uses_zero_provider_calls_for_clear_fast_path_and_calls_on_defer():
    provider = Provider({
        "status": "resolved",
        "goals": [{"kind": "order_status", "order_id": "DP1234"}],
    })
    cascade = CascadedTargetUnderstanding(
        BoundedTargetUnderstanding(), StructuredTargetCommandRouter(provider),
    )
    clear, _, _ = _invoke(cascade, "查订单 DP1234 物流")
    semantic, _, _ = _invoke(cascade, "帮我看看 DP1234 走到哪一步了")

    assert clear.reason_code == "BOUNDED_FAST_PATH"
    assert semantic.reason_code == "STRUCTURED_SEMANTIC_ROUTER"
    assert len(provider.calls) == 1


def test_anthropic_provider_normalizes_json_text_block():
    class Messages:
        async def create(self, **kwargs):
            assert kwargs["temperature"] == 0
            return SimpleNamespace(content=(SimpleNamespace(
                type="text",
                text='```json\n{"status":"out_of_scope"}\n```',
            ),))

    provider = AnthropicTargetSemanticProvider(
        SimpleNamespace(messages=Messages()), model="model-test",
    )
    result = asyncio.run(provider.route({"message": "hello"}))
    assert result == {"status": "out_of_scope"}
