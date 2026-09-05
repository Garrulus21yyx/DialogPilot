import asyncio
from dataclasses import replace
from types import SimpleNamespace

from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.conversation_agent import ConversationAgent
from application.target_understanding import (
    BoundedTargetUnderstanding,
    CascadedTargetUnderstanding,
)
from application.target_conversation_manager import (
    TargetContextMessage,
    TargetContextProjectionStatus,
    TargetContextSummary,
    TargetTurnContext,
)
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    RouteMode,
    RoutePolicy,
    TurnPlanCompiler,
    TurnProposal,
)
from core.identity import IdentityFactory
from infrastructure.target_conversation_provider import (
    AnthropicConversationPlanningProvider,
)


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


def _invoke(understanding, text, *, fields=()):
    state = _state()
    observations = TurnObservations(text, fields)
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


class Provider:
    version = "provider-test-v1"

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    async def plan(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.value


def test_conversation_agent_compiles_only_observed_entities_into_registry_command():
    provider = Provider({
        "status": "resolved",
        "goals": [{"goal_id": "where", "kind": "order_status", "order_id": "DP1234"}],
    })
    proposal, state, registry = _invoke(
        ConversationAgent(provider), "帮我看看 DP1234 走到哪一步了",
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


def test_conversation_agent_rejects_hallucinated_entity_and_unknown_goal():
    invented = Provider({
        "status": "resolved",
        "goals": [{"kind": "refund_status", "order_id": "DP9999"}],
    })
    proposal, _, _ = _invoke(
        ConversationAgent(invented), "查询 DP1234",
    )
    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT

    unknown = Provider({
        "status": "resolved", "goals": [{"kind": "delete_account"}],
    })
    proposal, _, _ = _invoke(ConversationAgent(unknown), "删除账户")
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
        ConversationAgent(provider), text,
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
    rejected, _, _ = _invoke(ConversationAgent(invented), text)
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


def test_security_review_is_direct_but_account_freeze_is_a_governed_flow():
    review, _, registry = _invoke(
        BoundedTargetUnderstanding(),
        "这次异常登录不是我操作的，帮我查一下",
    )
    freeze, state, _ = _invoke(
        BoundedTargetUnderstanding(),
        "立即冻结账户",
    )

    review_command = review.commands[0]
    freeze_command = freeze.commands[0]
    assert review_command.kind is CommandKind.DIRECT_TOOL
    assert review_command.tool_id == "account_security_event_list"
    assert review_command.skill_id is None
    assert freeze_command.kind is CommandKind.PREPARE_WORKFLOW
    assert freeze_command.action_ref == "account.freeze:v1"
    assert freeze_command.flow_ref == "freeze_account:v1"
    assert freeze_command.skill_id is None
    assert all("security" not in skill.skill_id for skill in registry.skills)

    plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(freeze, state, registry),
        state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            request_id="freeze-request",
        ),
    )
    assert plan.transitions.mutations[0].flow_ref == "freeze_account:v1"
    assert plan.work.items[0].allowed_tools == ("account_security_state",)


def test_conversation_agent_security_goals_cannot_select_unregistered_capabilities():
    review, _, _ = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{"kind": "security_review"}],
        })),
        "检查最近的安全事件",
    )
    freeze, _, _ = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{"kind": "freeze_account"}],
        })),
        "冻结我的账户",
    )

    assert review.commands[0].tool_id == "account_security_event_list"
    assert freeze.commands[0].action_ref == "account.freeze:v1"


def test_security_signal_preempts_only_registry_marked_business_writes():
    proposal, state, registry = _invoke(
        BoundedTargetUnderstanding(),
        "账号被盗，帮我查异常登录，同时把订单 DP1234 退款",
    )

    validated = RoutePolicy().accept(proposal, state, registry)
    plan = TurnPlanCompiler().compile(
        validated,
        state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="security-preemption",
        ),
    )

    assert validated.reason_code == "SECURITY_PREEMPTED_NONESSENTIAL_WRITES"
    assert [item.proposal.command_id for item in validated.commands] == [
        "security-review",
    ]
    assert plan.route.mode is RouteMode.DIRECT
    assert plan.transitions is None
    assert plan.work.items[0].allowed_tools == ("account_security_event_list",)


def test_security_signal_keeps_human_handoff_as_an_essential_coordination_action():
    proposal, state, registry = _invoke(
        BoundedTargetUnderstanding(),
        "账号被盗，我要人工客服处理",
    )

    validated = RoutePolicy().accept(proposal, state, registry)

    assert {item.proposal.command_id for item in validated.commands} == {
        "security-review", "human-handoff",
    }
    assert validated.reason_code == "BOUNDED_FAST_PATH"


def test_every_registry_marked_action_is_preempted_before_work_plan_compilation():
    state = _state()
    registry = build_default_capability_registry("tenant-a")
    interruptible = tuple(
        action for action in registry.actions
        if action.interruptible_by_security
    )

    assert {item.action_id for item in interruptible} == {
        "refund.request.create",
        "order.cancel",
        "order.shipping_address.change",
    }
    for action in interruptible:
        preparation = action.preparation
        assert preparation is not None
        proposal = TurnProposal(
            ProposalDisposition.RESOLVED,
            (
                CommandProposal(
                    "security-review",
                    CommandKind.DIRECT_TOOL,
                    "account_security",
                    "Review recent account security events",
                    requirement_ids=("account.security_events",),
                    tool_id="account_security_event_list",
                ),
                CommandProposal(
                    f"prepare:{action.action_id}",
                    CommandKind.PREPARE_WORKFLOW,
                    action.owner_agent,
                    "Prepare an interruptible write",
                    requirement_ids=(preparation.requirement_id,),
                    flow_ref=action.flow_ref,
                    action_ref=action.ref,
                    target_entity_ref="entity:test",
                ),
            ),
            "ADVERSARIAL_SECURITY_COMPOUND",
        )

        validated = RoutePolicy().accept(proposal, state, registry)
        plan = TurnPlanCompiler().compile(
            validated,
            state,
            registry,
            IdentityFactory().create_invocation(
                tenant_id="tenant-a", user_id="user-a",
                conversation_id="conversation-a",
                request_id=f"preempt-{action.action_id}",
            ),
        )

        assert [item.proposal.command_id for item in validated.commands] == [
            "security-review",
        ]
        assert plan.transitions is None
        assert len(plan.work.items) == 1


def test_compound_product_goal_delegates_one_domain_agent_with_capability_envelope():
    provider = Provider({
        "status": "resolved",
        "goals": [{"kind": "product_assistance", "asset_id": "IMG9"}],
    })
    proposal, state, registry = _invoke(
        ConversationAgent(provider),
        "识别图片 IMG9 中的商品，并根据说明告诉我是否支持 Mac",
        fields=(("asset_id", "IMG9"),),
    )

    command = proposal.commands[0]
    assert command.kind is CommandKind.DELEGATE_TASK
    assert command.target_agent == "product_technical"
    plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(proposal, state, registry),
        state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a",
            user_id="user-a",
            conversation_id="conversation-a",
            request_id="compound-product-request",
        ),
    )
    item = plan.work.items[0]
    assert plan.route.mode is RouteMode.AGENT_TASK
    assert item.skill_hint is None
    assert item.allowed_skills == ("product_identification",)
    assert set(item.allowed_tools) == {
        "media_read", "catalog_search", "knowledge_search",
    }


def test_media_text_is_direct_but_visual_reasoning_delegates_one_agent():
    text_proposal, text_state, registry = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{"kind": "media_text_read", "asset_id": "IMG9"}],
        })),
        "读取图片 IMG9 中的错误码",
        fields=(("asset_id", "IMG9"),),
    )
    visual_proposal, visual_state, _ = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{"kind": "media_visual_analysis", "asset_id": "IMG9"}],
        })),
        "图片 IMG9 右上角是否破损？",
        fields=(("asset_id", "IMG9"),),
    )

    direct = TurnPlanCompiler().compile(
        RoutePolicy().accept(text_proposal, text_state, registry),
        text_state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="media-text",
        ),
    )
    delegated = TurnPlanCompiler().compile(
        RoutePolicy().accept(visual_proposal, visual_state, registry),
        visual_state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="media-visual",
        ),
    )

    assert direct.route.mode is RouteMode.DIRECT
    assert direct.work.items[0].allowed_tools == ("media_read",)
    assert delegated.route.mode is RouteMode.AGENT_TASK
    assert delegated.work.items[0].allowed_tools == ("media_observe",)
    assert delegated.work.items[0].allowed_skills == ()


def test_asset_presence_alone_does_not_imply_product_identification():
    proposal, _, _ = _invoke(
        BoundedTargetUnderstanding(),
        "请看看附件 IMG9",
        fields=(("asset_id", "IMG9"),),
    )

    assert proposal.disposition is ProposalDisposition.CLARIFY
    assert proposal.commands == ()


def test_explicit_product_model_request_with_asset_is_identification():
    proposal, state, registry = _invoke(
        BoundedTargetUnderstanding(),
        "退款 DP1234 状态，还有这个商品型号",
        fields=(("asset_id", "IMG9"),),
    )

    plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(proposal, state, registry),
        state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="explicit-model",
        ),
    )

    assert plan.route.mode is RouteMode.MIXED
    assert {item.owner_agent for item in plan.work.items} == {
        "billing_refund", "product_technical",
    }


def test_conversation_agent_preserves_typed_rejection_and_provider_failure():
    insufficient = Provider({
        "status": "insufficient_context", "missing_fields": ["customer_service_goal"],
    })
    proposal, _, _ = _invoke(
        ConversationAgent(insufficient), "还是那个问题",
    )
    assert proposal.disposition is ProposalDisposition.CLARIFY
    assert proposal.missing_inputs == ("customer_service_goal",)

    failed = Provider(error=TimeoutError("provider timeout"))
    proposal, _, _ = _invoke(ConversationAgent(failed), "帮我处理一下")
    assert proposal.disposition is ProposalDisposition.PROVIDER_FAILURE


def test_conversation_agent_receives_bounded_memory_evidence_as_data():
    provider = Provider({
        "status": "insufficient_context",
        "missing_fields": ["customer_service_goal"],
    })
    state = _state()
    observations = TurnObservations(
        "继续上次那个问题",
        understanding_evidence=((
            "memory.service_episode",
            {"purpose_outcome": "UNIQUE_BINDING", "hits": [{"episode_id": "e1"}]},
        ),),
    )
    deterministic = DeterministicResolver().resolve(observations, state)
    asyncio.run(ConversationAgent(provider).plan(
        observations,
        state,
        deterministic,
        build_default_capability_registry("tenant-a"),
    ))

    assert provider.calls[0]["understanding_evidence"] == [{
        "kind": "memory.service_episode",
        "value": {
            "purpose_outcome": "UNIQUE_BINDING",
            "hits": [{"episode_id": "e1"}],
        },
    }]


def test_conversation_agent_receives_typed_current_conversation_context():
    provider = Provider({
        "status": "insufficient_context",
        "missing_fields": ["customer_service_goal"],
    })
    state = _state()
    observations = TurnObservations("继续处理")
    deterministic = DeterministicResolver().resolve(observations, state)
    context = TargetTurnContext(
        recent_messages=(TargetContextMessage(
            "assistant", "请提供订单号", "event:9", 9,
        ),),
        summary=TargetContextSummary(
            "用户正在处理订单问题", "summary:1-8", 8,
        ),
        projection_status=TargetContextProjectionStatus.READY,
        source_watermark=9,
        projection_reason_codes=(),
    )

    asyncio.run(ConversationAgent(provider).plan(
        observations,
        state,
        deterministic,
        build_default_capability_registry("tenant-a"),
        context,
    ))

    assert provider.calls[0]["conversation_context"] == {
        "projection_status": "READY",
        "source_watermark": 9,
        "reason_codes": [],
        "summary": {
            "content": "用户正在处理订单问题",
            "source_ref": "summary:1-8",
            "covered_until_seq": 8,
            "producer_version": "conversation-memory-v1",
        },
        "recent_messages": [{
            "role": "assistant",
            "content": "请提供订单号",
            "source_ref": "event:9",
            "seq": 9,
            "observed_at": None,
        }],
        "evidence_refs": [],
    }


def test_cascade_uses_zero_provider_calls_for_clear_fast_path_and_calls_on_defer():
    provider = Provider({
        "status": "resolved",
        "goals": [{"kind": "order_status", "order_id": "DP1234"}],
    })
    cascade = CascadedTargetUnderstanding(
        BoundedTargetUnderstanding(), ConversationAgent(provider),
    )
    clear, _, _ = _invoke(cascade, "查订单 DP1234 物流")
    semantic, _, _ = _invoke(cascade, "帮我看看 DP1234 走到哪一步了")

    assert clear.reason_code == "BOUNDED_FAST_PATH"
    assert semantic.reason_code == "CONVERSATION_AGENT_PLAN"
    assert len(provider.calls) == 1


def test_anthropic_provider_normalizes_json_text_block():
    class Messages:
        async def create(self, **kwargs):
            assert kwargs["temperature"] == 0
            return SimpleNamespace(content=(SimpleNamespace(
                type="text",
                text='```json\n{"status":"out_of_scope"}\n```',
            ),))

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model="model-test",
    )
    result = asyncio.run(provider.plan({"message": "hello"}))
    assert result == {"status": "out_of_scope"}
