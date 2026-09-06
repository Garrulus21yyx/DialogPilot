from core.model_policy import ModelProfile
import asyncio
from dataclasses import replace
from types import SimpleNamespace

from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.conversation_agent import ConversationAgent
from application.context_budget import ContextBudgetManager
from application.target_understanding import (
    CascadedTargetUnderstanding,
    StateBoundTargetUnderstanding,
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


def test_anthropic_provider_uses_the_installed_messages_contract():
    class Messages:
        async def create(self, *, model, max_tokens, system, messages):
            assert model == "model-a"
            assert max_tokens == 800
            assert system
            assert messages[0]["role"] == "user"
            return SimpleNamespace(content=(SimpleNamespace(
                type="text",
                text='{"status":"out_of_scope"}',
            ),))

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()),
        model_profile=ModelProfile("model-a"),
    )

    assert asyncio.run(provider.plan({"message": "unsupported"})) == {
        "status": "out_of_scope",
    }


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


def test_conversation_agent_revises_or_cancels_only_named_active_work():
    registry = build_default_capability_registry("tenant-a")
    state = _state()
    identity_factory = IdentityFactory()
    first_identity = identity_factory.create_invocation(
        tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="first",
    )
    initial = CommandProposal(
        "faq", CommandKind.DIRECT_TOOL, "general", "answer first question",
        requirement_ids=("knowledge.active_source",),
        tool_id="knowledge_search",
    )
    first_plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(
            TurnProposal(ProposalDisposition.RESOLVED, (initial,), "TEST"),
            state,
            registry,
        ),
        state,
        registry,
        first_identity,
    )
    state = state.accept_work_items(
        first_plan.work.items, invocation_key=str(first_identity.invocation_key),
    )
    control = state.active_work_controls[0]

    correction_provider = Provider({
        "status": "resolved",
        "goals": [{
            "goal_id": "corrected",
            "kind": "general_qa",
            "revises_control_id": control.control_id,
        }],
    })
    observations = TurnObservations("更正一下，我问的是另一项政策")
    deterministic = DeterministicResolver().resolve(observations, state)
    correction = asyncio.run(ConversationAgent(correction_provider).plan(
        observations, state, deterministic, registry, TargetTurnContext(),
    ))
    corrected_plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(correction, state, registry),
        state,
        registry,
        identity_factory.create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="second",
        ),
    )

    assert correction_provider.calls[0]["active_work_controls"][0][
        "control_id"
    ] == control.control_id
    assert corrected_plan.work.items[0].control.control_id == control.control_id
    assert corrected_plan.work.items[0].control.revision == 2

    cancel_provider = Provider({
        "status": "resolved",
        "goals": [{
            "goal_id": "cancel",
            "kind": "cancel_active_work",
            "revises_control_id": control.control_id,
        }],
    })
    cancellation = asyncio.run(ConversationAgent(cancel_provider).plan(
        TurnObservations("先停一下"), state,
        DeterministicResolver().resolve(TurnObservations("先停一下"), state),
        registry,
        TargetTurnContext(),
    ))
    cancel_plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(cancellation, state, registry),
        state,
        registry,
        identity_factory.create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="cancel",
        ),
    )

    assert cancel_plan.work is None
    assert cancel_plan.control_mutations[0].control_id == control.control_id

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


def test_address_change_stays_a_flow_and_missing_address_stays_typed():
    proposal, _, registry = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{
                "kind": "change_address",
                "order_id": "DP1234",
                "new_address": "Berlin Example Street 9",
            }],
        })),
        "把订单 DP1234 的地址改成 Berlin Example Street 9",
    )

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert proposal.commands[0].kind is CommandKind.PREPARE_WORKFLOW
    assert proposal.commands[0].skill_id is None
    assert proposal.commands[0].action_ref == "order.shipping_address.change:v1"
    assert all("address" not in skill.skill_id for skill in registry.skills)

    missing, _, _ = _invoke(
        ConversationAgent(Provider({
            "status": "insufficient_context",
            "missing_fields": ["new_address"],
        })),
        "订单 DP1234 修改地址",
    )
    assert missing.disposition is ProposalDisposition.CLARIFY
    assert missing.reason_code == "SEMANTIC_INSUFFICIENT_CONTEXT"


def test_security_review_is_direct_but_account_freeze_is_a_governed_flow():
    review, _, registry = _invoke(
        ConversationAgent(Provider({
            "status": "resolved", "goals": [{"kind": "security_review"}],
        })),
        "这次异常登录不是我操作的，帮我查一下",
    )
    freeze, state, _ = _invoke(
        ConversationAgent(Provider({
            "status": "resolved", "goals": [{"kind": "freeze_account"}],
        })),
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
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [
                {"goal_id": "security", "kind": "security_review"},
                {
                    "goal_id": "refund",
                    "kind": "execute_refund",
                    "order_id": "DP1234",
                },
            ],
        })),
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
        "security",
    ]
    assert plan.route.mode is RouteMode.DIRECT
    assert plan.transitions is None
    assert plan.work.items[0].allowed_tools == ("account_security_event_list",)


def test_security_signal_keeps_human_handoff_as_an_essential_coordination_action():
    proposal, state, registry = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [
                {"goal_id": "security", "kind": "security_review"},
                {"goal_id": "handoff", "kind": "human_handoff"},
            ],
        })),
        "账号被盗，我要人工客服处理",
    )

    validated = RoutePolicy().accept(proposal, state, registry)

    assert {item.proposal.command_id for item in validated.commands} == {
        "security", "handoff",
    }
    assert validated.reason_code == "CONVERSATION_AGENT_PLAN"


def test_single_handoff_and_knowledge_paths_keep_their_route_semantics():
    handoff, state, registry = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{"kind": "human_handoff"}],
        })),
        "我要人工客服处理",
    )
    knowledge, knowledge_state, _ = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{"kind": "refund_policy"}],
        })),
        "退款通常需要多久",
    )
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="route-semantics",
    )

    handoff_plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(handoff, state, registry),
        state,
        registry,
        identity,
    )
    knowledge_plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(knowledge, knowledge_state, registry),
        knowledge_state,
        registry,
        identity,
    )

    assert handoff_plan.route.mode is RouteMode.HANDOFF
    assert knowledge_plan.route.mode is RouteMode.KNOWLEDGE_QA


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
        ConversationAgent(Provider({
            "status": "insufficient_context",
            "missing_fields": ["customer_service_goal"],
        })),
        "请看看附件 IMG9",
        fields=(("asset_id", "IMG9"),),
    )

    assert proposal.disposition is ProposalDisposition.CLARIFY
    assert proposal.commands == ()


def test_explicit_product_model_request_with_asset_is_identification():
    proposal, state, registry = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [
                {
                    "goal_id": "refund",
                    "kind": "refund_status",
                    "order_id": "DP1234",
                },
                {
                    "goal_id": "product",
                    "kind": "product_identification",
                    "asset_id": "IMG9",
                },
            ],
        })),
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


def test_conversation_agent_preserves_cross_goal_dependencies_in_work_plan():
    proposal, state, registry = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [
                {
                    "goal_id": "identify",
                    "kind": "product_identification",
                    "asset_id": "IMG9",
                },
                {
                    "goal_id": "handoff",
                    "kind": "human_handoff",
                    "depends_on": ["identify"],
                },
            ],
        })),
        "识别商品后把结果一并交给人工处理",
        fields=(("asset_id", "IMG9"),),
    )

    assert proposal.commands[1].dependencies == ("identify",)
    plan = TurnPlanCompiler().compile(
        RoutePolicy().accept(proposal, state, registry),
        state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="dependent-goals",
        ),
    )
    assert plan.work.items[1].dependencies == (
        plan.work.items[0].work_item_id,
    )


def test_conversation_agent_rejects_dependency_outside_the_proposed_goals():
    proposal, _, _ = _invoke(
        ConversationAgent(Provider({
            "status": "resolved",
            "goals": [{
                "goal_id": "handoff",
                "kind": "human_handoff",
                "depends_on": ["missing-goal"],
            }],
        })),
        "转人工",
    )

    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


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


def test_conversation_agent_reports_context_budget_without_calling_provider():
    provider = Provider({"status": "out_of_scope"})
    proposal, _, _ = _invoke(
        ConversationAgent(
            provider,
            context_budget=ContextBudgetManager(
                context_window_tokens=300,
                reserved_output_tokens=100,
                protocol_reserve_tokens=100,
            ),
        ),
        "当前请求必须保留" * 500,
    )
    assert proposal.disposition is ProposalDisposition.PROVIDER_FAILURE
    assert proposal.reason_code == "CONTEXT_BUDGET_EXCEEDED"
    assert provider.calls == []


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


def test_cascade_uses_planner_when_no_state_or_encoder_path_resolves():
    provider = Provider({
        "status": "resolved",
        "goals": [{"kind": "order_status", "order_id": "DP1234"}],
    })
    cascade = CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(), ConversationAgent(provider),
    )
    clear, _, _ = _invoke(cascade, "查订单 DP1234 物流")
    semantic, _, _ = _invoke(cascade, "帮我看看 DP1234 走到哪一步了")

    assert clear.reason_code == "CONVERSATION_AGENT_PLAN"
    assert semantic.reason_code == "CONVERSATION_AGENT_PLAN"
    assert len(provider.calls) == 2


def test_anthropic_provider_normalizes_json_text_block():
    class Messages:
        async def create(self, **kwargs):
            assert "temperature" not in kwargs
            return SimpleNamespace(content=(SimpleNamespace(
                type="text",
                text='```json\n{"status":"out_of_scope"}\n```',
            ),))

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=ModelProfile("model-test"),
    )
    result = asyncio.run(provider.plan({"message": "hello"}))
    assert result == {"status": "out_of_scope"}


def test_malformed_provider_transport_is_not_reported_as_an_outage():
    class Messages:
        async def create(self, **_kwargs):
            return SimpleNamespace(content=(SimpleNamespace(
                type="text",
                text="not-json",
            ),))

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=ModelProfile("model-test"),
    )
    proposal, _, _ = _invoke(ConversationAgent(provider), "帮我处理")

    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT
    assert proposal.reason_code == "CONVERSATION_PROVIDER_OUTPUT_INVALID"


def test_knowledge_direct_goal_preserves_explicit_applicability_options():
    provider = Provider({'status':'resolved','goals':[{
        'kind':'refund_policy', 'resolved_query':'2023年购买商品适用什么退款政策',
        'knowledge_options':{'as_of':'2023-06-01T00:00:00+00:00','applicable_region':'CN'},
    }]})
    proposal, _, _ = _invoke(ConversationAgent(provider), '2023年在中国购买商品适用什么退款政策')
    arguments = {item.name:item.value for item in proposal.commands[0].arguments}
    assert arguments['as_of']=='2023-06-01T00:00:00+00:00'
    assert arguments['applicable_region']=='CN'
