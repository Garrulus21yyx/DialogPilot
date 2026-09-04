import pytest

from application.authority_policy import AuthoritySupport, FactRequirement, RequirementEffect
from application.capability_registry import (
    ActionDefinition,
    ActionPreparationDefinition,
    AgentDefinition,
    ApprovalPolicy,
    CapabilityEffect,
    CapabilityRegistryBundle,
    CapabilityRisk,
    FlowDefinition,
    SkillDefinition,
    ToolDefinition,
    VerificationProfile,
)
from application.conversation_state import (
    ConversationState,
    PendingApprovalState,
    WorkstreamState,
    WorkstreamStatus,
)
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    MutationApplyStage,
    ProposalDisposition,
    RouteMode,
    RoutePolicy,
    TurnPlanCompiler,
    TurnPlanningError,
    TurnProposal,
)
from application.work_item import ArgumentValue, ControlMode
from core.identity import IdentityFactory


def _requirement(requirement_id, effect, tool_id):
    return FactRequirement(
        requirement_id,
        requirement_id,
        ("value",),
        60 if effect is RequirementEffect.READ else None,
        effect,
        (tool_id,),
        (),
        "write-receipt-v1" if effect is RequirementEffect.WRITE else "",
        AuthoritySupport.SUPPORTED,
        f"{requirement_id}-owner-v1",
    )


def _registry():
    profile = VerificationProfile("default", "v1", ("authority", "coverage"))
    tools = (
        ToolDefinition(
            "order_lookup", "v1", "order-input-v1", "order-output-v1",
            CapabilityEffect.READ, CapabilityRisk.MEDIUM, "order.current_state",
            profile.ref,
        ),
        ToolDefinition(
            "catalog_search", "v1", "catalog-input-v1", "catalog-output-v1",
            CapabilityEffect.READ, CapabilityRisk.LOW, "product.canonical_model",
            profile.ref,
        ),
        ToolDefinition(
            "refund_request_create", "v1", "refund-input-v1", "refund-output-v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH, "refund.request_action",
            profile.ref, "write-receipt-v1",
        ),
        ToolDefinition(
            "refund_eligibility_check", "v1", "eligibility-input-v1",
            "eligibility-output-v1", CapabilityEffect.READ, CapabilityRisk.MEDIUM,
            "refund.eligibility", profile.ref,
        ),
    )
    agents = (
        AgentDefinition(
            "order_logistics", "v1", ("order_lookup",), (),
            "order-model-v1", "order-context-v1", profile.ref,
        ),
        AgentDefinition(
            "product_technical", "v1", ("catalog_search",), ("identify_product",),
            "product-model-v1", "product-context-v1", profile.ref,
        ),
        AgentDefinition(
            "billing_refund", "v1",
            ("refund_eligibility_check", "refund_request_create"), (),
            "refund-model-v1", "refund-context-v1", profile.ref,
        ),
    )
    return CapabilityRegistryBundle(
        "tenant-a",
        "customer-service-v1",
        agents,
        (SkillDefinition(
            "identify_product", "v1", "product_technical", "Identify a product",
            ("asset_id",), (), ("product.canonical_model",), ("catalog_search",),
            CapabilityEffect.READ, CapabilityRisk.LOW, profile.ref,
        ),),
        (FlowDefinition(
            "execute_refund", "v1", "billing_refund",
            ("CHECK", "APPROVE", "EXECUTE", "RECONCILE", "COMPLETE"),
            "CHECK", ("COMPLETE",),
            ("refund_eligibility_check", "refund_request_create"),
        ),),
        (ActionDefinition(
            "refund.request.create", "v1", "billing_refund", "execute_refund:v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH, ("refund.request_action",),
            ("refund_request_create",), ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            "write-receipt-v1", "refund-reconcile-v1", profile.ref,
            ActionPreparationDefinition.create(
                tool_id="refund_eligibility_check",
                requirement_id="refund.eligibility",
                readiness_field="eligible",
                readiness_value=True,
                target_version_field="order_version",
                target_version_argument="expected_order_version",
            ),
        ),),
        (
            _requirement("order.current_state", RequirementEffect.READ, "order_lookup"),
            _requirement("product.canonical_model", RequirementEffect.READ, "catalog_search"),
            _requirement("refund.request_action", RequirementEffect.WRITE, "refund_request_create"),
            _requirement("refund.eligibility", RequirementEffect.READ, "refund_eligibility_check"),
        ),
        tools,
        (profile,),
    )


def _state():
    return ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )


def _invocation():
    return IdentityFactory(lambda: "fixed").create_invocation(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conversation-a",
        request_id="request-a",
    )


def _compile(*commands):
    registry = _registry()
    state = _state()
    accepted = RoutePolicy().accept(
        TurnProposal(ProposalDisposition.RESOLVED, commands, "UNDERSTOOD"),
        state,
        registry,
    )
    return TurnPlanCompiler().compile(accepted, state, registry, _invocation())


def test_direct_tool_is_the_shortest_path_and_registry_owns_its_risk():
    plan = _compile(CommandProposal(
        "order-1",
        CommandKind.DIRECT_TOOL,
        "order_logistics",
        "Query order status",
        (ArgumentValue.create("order_id", "DP1234"),),
        ("order.current_state",),
        tool_id="order_lookup",
    ))

    assert plan.route.mode is RouteMode.DIRECT
    assert plan.route.risk is CapabilityRisk.MEDIUM
    assert plan.work.items[0].control_mode is ControlMode.DIRECT
    assert plan.work.items[0].allowed_tools == ("order_lookup",)


def test_multi_domain_is_projected_only_from_delegated_work_plan():
    plan = _compile(
        CommandProposal(
            "product-1", CommandKind.RUN_SKILL, "product_technical",
            "Identify product", (ArgumentValue.create("asset_id", "IMG9"),),
            ("product.canonical_model",), skill_id="identify_product",
        ),
        CommandProposal(
            "order-1", CommandKind.DELEGATE_TASK, "order_logistics",
            "Investigate order status", requirement_ids=("order.current_state",),
            dependencies=("product-1",),
        ),
    )

    assert plan.route.mode is RouteMode.MULTI_DOMAIN
    assert [len(wave) for wave in plan.work.execution_waves()] == [1, 1]
    assert plan.work.items[1].dependencies == (plan.work.items[0].work_item_id,)


def test_mixed_direct_and_delegated_work_is_not_mislabeled_multi_agent():
    plan = _compile(
        CommandProposal(
            "order-1", CommandKind.DIRECT_TOOL, "order_logistics", "Query order",
            requirement_ids=("order.current_state",), tool_id="order_lookup",
        ),
        CommandProposal(
            "product-1", CommandKind.RUN_SKILL, "product_technical", "Identify product",
            requirement_ids=("product.canonical_model",), skill_id="identify_product",
        ),
    )

    assert plan.route.mode is RouteMode.MIXED


def test_write_plan_derives_operation_identity_and_plan_accepted_flow_mutation():
    plan = _compile(CommandProposal(
        "refund-1",
        CommandKind.START_WORKFLOW,
        "billing_refund",
        "Create refund",
        (ArgumentValue.create("order_id", "DP1234"),),
        ("refund.request_action",),
        flow_ref="execute_refund:v1",
        action_ref="refund.request.create:v1",
        target_entity_ref="order:DP1234",
        target_entity_version="order:DP1234:v7",
        approval_binding="approval-1:v1",
    ))

    item = plan.work.items[0]
    assert item.control_mode is ControlMode.WORKFLOW
    assert item.operation_key.startswith("operation:v1:")
    assert item.reconciliation_policy == "refund-reconcile-v1"
    assert item.action_ref == "refund.request.create:v1"
    assert item.approval_policy is ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED
    assert plan.transitions.mutations[0].apply_stage is MutationApplyStage.PLAN_ACCEPTED


def test_workflow_preparation_is_read_only_but_starts_versioned_workstream():
    plan = _compile(CommandProposal(
        "prepare-refund",
        CommandKind.PREPARE_WORKFLOW,
        "billing_refund",
        "Check eligibility",
        (ArgumentValue.create("order_id", "DP1234"),),
        ("refund.eligibility",),
        flow_ref="execute_refund:v1",
        action_ref="refund.request.create:v1",
        target_entity_ref="order:DP1234",
    ))

    item = plan.work.items[0]
    mutation = plan.transitions.mutations[0]
    assert item.control_mode is ControlMode.DIRECT
    assert item.effect is CapabilityEffect.READ
    assert item.operation_key is None
    assert mutation.action_ref == "refund.request.create:v1"
    assert mutation.preparation_requirement_id == "refund.eligibility"
    assert mutation.readiness_field == "eligible"
    assert mutation.readiness_value_json == "true"
    assert mutation.target_version_field == "order_version"
    assert mutation.target_version_argument == "expected_order_version"


def test_workflow_continuation_requires_consumed_bound_approval():
    registry = _registry()
    state = _state().start_workstream(WorkstreamState(
        "refund-ws", "billing_refund", "execute_refund:v1", "CHECK",
        WorkstreamStatus.ACTIVE, 1, flow_ref="execute_refund:v1",
    ))
    state = state.wait_for_approval(PendingApprovalState(
        "approval-1", 1, "refund-ws", "prepare-work",
        "refund.request.create:v1", "operation-1", "order:DP1234", "7",
        "2099-01-01T00:00:00+00:00",
        (
            ArgumentValue.create("order_id", "DP1234"),
            ArgumentValue.create("reason", "用户申请退款"),
            ArgumentValue.create("expected_order_version", 7),
        ),
    ))
    command = CommandProposal(
        "continue-refund", CommandKind.CONTINUE_WORKFLOW, "billing_refund",
        "Execute approved refund",
        state.pending_approval.arguments,
        ("refund.request_action",),
        flow_ref="execute_refund:v1",
        action_ref="refund.request.create:v1",
        target_entity_ref="order:DP1234",
        target_entity_version="7",
        approval_binding="approval-1",
        approval_signal_version=1,
        operation_key="operation-1",
    )
    proposal = TurnProposal(ProposalDisposition.RESOLVED, (command,), "RESUME")

    with pytest.raises(TurnPlanningError, match="no consumed approval"):
        RoutePolicy().accept(proposal, state, registry)

    approved = state.consume_approval(
        approval_id="approval-1", approval_version=1, approved=True,
    )
    tampered = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(**{
            **command.__dict__,
            "operation_key": "operation-other",
        }),),
        "RESUME",
    )
    with pytest.raises(TurnPlanningError, match="differs from accepted approval"):
        RoutePolicy().accept(tampered, approved, registry)
    tampered_arguments = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(**{
            **command.__dict__,
            "arguments": (
                ArgumentValue.create("order_id", "DP9999"),
                *command.arguments[1:],
            ),
        }),),
        "RESUME",
    )
    with pytest.raises(TurnPlanningError, match="arguments differ"):
        RoutePolicy().accept(tampered_arguments, approved, registry)

    accepted = RoutePolicy().accept(proposal, approved, registry)
    plan = TurnPlanCompiler().compile(
        accepted, approved, registry, _invocation(),
    )
    assert plan.transitions is None
    assert plan.work.items[0].operation_key == "operation-1"
    assert plan.work.items[0].target_entity_version == "7"


def test_route_policy_rejects_cross_agent_tool_and_uncontrolled_write():
    registry = _registry()
    state = _state()
    cross_agent = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "bad-1", CommandKind.DIRECT_TOOL, "product_technical", "Read order",
            requirement_ids=("order.current_state",), tool_id="order_lookup",
        ),),
        "UNDERSTOOD",
    )
    with pytest.raises(TurnPlanningError, match="outside agent allowlist"):
        RoutePolicy().accept(cross_agent, state, registry)

    write_as_delegation = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "bad-2", CommandKind.DELEGATE_TASK, "billing_refund", "Create refund",
            requirement_ids=("refund.request_action",),
        ),),
        "UNDERSTOOD",
    )
    with pytest.raises(TurnPlanningError, match="business writes must use a workflow"):
        RoutePolicy().accept(write_as_delegation, state, registry)


def test_compiler_rejects_state_changed_after_policy_acceptance():
    registry = _registry()
    state = _state()
    accepted = RoutePolicy().accept(TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "order-1", CommandKind.DIRECT_TOOL, "order_logistics", "Query order",
            requirement_ids=("order.current_state",), tool_id="order_lookup",
        ),),
        "UNDERSTOOD",
    ), state, registry)
    changed = state.start_workstream(WorkstreamState(
        "ws-1", "order_logistics", "order-status:v1", "ACTIVE",
        WorkstreamStatus.ACTIVE,
        1,
    ))

    with pytest.raises(TurnPlanningError, match="stale conversation state"):
        TurnPlanCompiler().compile(accepted, changed, registry, _invocation())


def test_terminal_clarification_compiles_without_work_or_agent_dispatch():
    registry = _registry()
    state = _state()
    accepted = RoutePolicy().accept(
        TurnProposal(ProposalDisposition.CLARIFY, (), "MISSING_ORDER", ("order_id",)),
        state,
        registry,
    )

    plan = TurnPlanCompiler().compile(accepted, state, registry, _invocation())

    assert plan.route.mode is RouteMode.CLARIFY
    assert plan.route.missing_inputs == ("order_id",)
    assert plan.work is None
