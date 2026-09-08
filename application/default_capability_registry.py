"""Executable customer-service capability bundle for Target Architecture v1."""
from __future__ import annotations

from application.authority_policy import AuthoritySupport, FactRequirement, RequirementEffect
from application.capability_registry import (
    ActionDefinition,
    ActionPreparationDefinition,
    ActionReconciliationDefinition,
    WriteRecoveryPolicy,
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


def build_default_capability_registry(tenant_id: str) -> CapabilityRegistryBundle:
    profile = VerificationProfile(
        "customer-service-default",
        "v1",
        ("authority", "requirement_coverage", "citation", "side_effect"),
    )
    tools = (
        _tool("knowledge_search", "knowledge.active_source", CapabilityRisk.LOW, profile.ref),
        _tool(
            "service_episode_search", "memory.service_episode",
            CapabilityRisk.LOW, profile.ref,
        ),
        _tool("media_read", "media.visible_text", CapabilityRisk.LOW, profile.ref),
        _tool(
            "media_observe", "media.visual_observation",
            CapabilityRisk.LOW, profile.ref,
        ),
        _tool("catalog_search", "product.canonical_model", CapabilityRisk.LOW, profile.ref),
        _tool("order_lookup", "order.current_state", CapabilityRisk.MEDIUM, profile.ref),
        _tool(
            "order_cancel_status", "order.cancellation_state",
            CapabilityRisk.MEDIUM, profile.ref,
        ),
        _tool(
            "order_cancel", "order.cancel_action", CapabilityRisk.HIGH,
            profile.ref, write=True,
        ),
        _tool(
            "shipping_address_change_status", "order.shipping_address_state",
            CapabilityRisk.MEDIUM, profile.ref,
        ),
        _tool(
            "shipping_address_change", "order.shipping_address_action",
            CapabilityRisk.HIGH, profile.ref, write=True,
        ),
        _tool("refund_status", "refund.current_state", CapabilityRisk.MEDIUM, profile.ref),
        _tool("refund_eligibility_check", "refund.eligibility", CapabilityRisk.MEDIUM, profile.ref),
        _tool(
            "refund_request_create", "refund.request_action", CapabilityRisk.HIGH,
            profile.ref, write=True,
        ),
        _tool(
            "support_ticket_create", "support.handoff_action", CapabilityRisk.HIGH,
            profile.ref, write=True,
        ),
        _tool(
            "support_ticket_by_operation", "support.ticket_state",
            CapabilityRisk.LOW, profile.ref,
        ),
        _tool(
            "account_security_event_list", "account.security_events",
            CapabilityRisk.MEDIUM, profile.ref,
        ),
        _tool(
            "account_security_state", "account.current_state",
            CapabilityRisk.HIGH, profile.ref,
        ),
        _tool(
            "account_freeze_status", "account.freeze_state",
            CapabilityRisk.HIGH, profile.ref,
        ),
        _tool(
            "account_freeze", "account.freeze_action",
            CapabilityRisk.CRITICAL, profile.ref, write=True,
        ),
    )
    skills = (
        _skill(
            "product_identification", "product_technical",
            "Identify a canonical product from media and catalog evidence",
            ("asset_id",), ("product.canonical_model",),
            ("media_read", "catalog_search"), CapabilityRisk.MEDIUM, profile.ref,
        ),
    )
    agents = (
        _agent(
            "general", (
                "knowledge_search", "service_episode_search",
                "media_read", "media_observe",
            ), (),
            profile.ref,
        ),
        _agent(
            "product_technical",
            (
                "media_read", "media_observe", "catalog_search", "knowledge_search",
                "service_episode_search",
            ),
            ("product_identification",),
            profile.ref,
        ),
        _agent(
            "order_logistics",
            (
                "order_lookup", "order_cancel_status", "order_cancel",
                "shipping_address_change_status", "shipping_address_change",
                "service_episode_search",
                "media_read", "media_observe",
            ),
            (),
            profile.ref,
        ),
        _agent(
            "billing_refund",
            (
                "knowledge_search", "service_episode_search", "order_lookup", "refund_status",
                "refund_eligibility_check", "refund_request_create",
                "media_read", "media_observe",
            ),
            (),
            profile.ref,
        ),
        _agent(
            "account_security",
            (
                "service_episode_search", "account_security_event_list", "account_security_state",
                "account_freeze_status", "account_freeze",
                "media_read", "media_observe",
            ),
            (),
            profile.ref,
        ),
        _agent(
            "human_service",
            ("support_ticket_create", "support_ticket_by_operation"),
            (),
            profile.ref,
        ),
    )
    flows = (
        FlowDefinition(
            "execute_refund", "v1", "billing_refund",
            (
                "CHECK_ELIGIBILITY", "CALCULATE_AMOUNT", "CHECK_APPROVAL",
                "EXECUTE", "RECONCILE", "COMPLETE", "CANCELLED",
            ),
            "CHECK_ELIGIBILITY", ("COMPLETE", "CANCELLED"),
            ("order_lookup", "refund_eligibility_check", "refund_request_create", "refund_status"),
        ),
        FlowDefinition(
            "cancel_order", "v1", "order_logistics",
            (
                "CHECK_ORDER", "CHECK_APPROVAL", "EXECUTE", "RECONCILE",
                "COMPLETE", "CANCELLED",
            ),
            "CHECK_ORDER", ("COMPLETE", "CANCELLED"),
            ("order_lookup", "order_cancel_status", "order_cancel"),
        ),
        FlowDefinition(
            "change_shipping_address", "v1", "order_logistics",
            (
                "CHECK_ORDER", "CHECK_APPROVAL", "EXECUTE", "RECONCILE",
                "COMPLETE", "CANCELLED",
            ),
            "CHECK_ORDER", ("COMPLETE", "CANCELLED"),
            (
                "order_lookup", "shipping_address_change_status",
                "shipping_address_change",
            ),
        ),
        FlowDefinition(
            "human_handoff", "v1", "human_service",
            ("PREPARE", "CHECK_POLICY", "CREATE_TICKET", "COMPLETE", "CANCELLED"),
            "PREPARE", ("COMPLETE", "CANCELLED"),
            ("support_ticket_create", "support_ticket_by_operation"),
        ),
        FlowDefinition(
            "freeze_account", "v1", "account_security",
            (
                "CHECK_ACCOUNT", "CHECK_APPROVAL", "EXECUTE", "RECONCILE",
                "COMPLETE", "CANCELLED",
            ),
            "CHECK_ACCOUNT", ("COMPLETE", "CANCELLED"),
            ("account_security_state", "account_freeze_status", "account_freeze"),
        ),
    )
    actions = (
        ActionDefinition(
            "refund.request.create", "v1", "billing_refund", "execute_refund:v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH, ("refund.request_action",),
            ("refund_request_create",), ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            "action-receipt-v1",
            ActionReconciliationDefinition(
                "refund_status",
                "refund.current_state",
                "operation_key",
                "operation_key",
                ("order_id",),
                "refund_id",
                WriteRecoveryPolicy("IDEMPOTENT_OPERATION"),
            ),
            profile.ref,
            ActionPreparationDefinition.create(
                tool_id="refund_eligibility_check",
                requirement_id="refund.eligibility",
                readiness_field="eligible",
                readiness_value=True,
                target_version_field="order_version",
                target_version_argument="expected_order_version",
            ),
            True,
        ),
        ActionDefinition(
            "support.handoff.create", "v1", "human_service", "human_handoff:v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH, ("support.handoff_action",),
            ("support_ticket_create",), ApprovalPolicy.USER_COMMAND_SUFFICIENT,
            "action-receipt-v1",
            ActionReconciliationDefinition(
                "support_ticket_by_operation",
                "support.ticket_state",
                "operation_key",
                "operation_key",
                (),
                "ticket_id",
                WriteRecoveryPolicy("RECEIPT_ONLY"),
            ),
            profile.ref,
        ),
        ActionDefinition(
            "order.cancel", "v1", "order_logistics", "cancel_order:v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH, ("order.cancel_action",),
            ("order_cancel",), ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            "action-receipt-v1",
            ActionReconciliationDefinition(
                "order_cancel_status",
                "order.cancellation_state",
                "operation_key",
                "operation_key",
                ("order_id",),
                "cancellation_id",
                WriteRecoveryPolicy("IDEMPOTENT_OPERATION"),
            ),
            profile.ref,
            ActionPreparationDefinition.create(
                tool_id="order_lookup",
                requirement_id="order.current_state",
                readiness_field="status",
                readiness_value="paid",
                target_version_field="version",
                target_version_argument="expected_order_version",
            ),
            True,
        ),
        ActionDefinition(
            "order.shipping_address.change", "v1", "order_logistics",
            "change_shipping_address:v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH,
            ("order.shipping_address_action",),
            ("shipping_address_change",),
            ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            "action-receipt-v1",
            ActionReconciliationDefinition(
                "shipping_address_change_status",
                "order.shipping_address_state",
                "operation_key",
                "operation_key",
                ("order_id", "new_address"),
                "change_id",
                WriteRecoveryPolicy("IDEMPOTENT_OPERATION"),
            ),
            profile.ref,
            ActionPreparationDefinition.create(
                tool_id="order_lookup",
                requirement_id="order.current_state",
                readiness_field="status",
                readiness_value="paid",
                target_version_field="version",
                target_version_argument="expected_order_version",
            ),
            True,
        ),
        ActionDefinition(
            "account.freeze", "v1", "account_security", "freeze_account:v1",
            CapabilityEffect.WRITE, CapabilityRisk.CRITICAL,
            ("account.freeze_action",),
            ("account_freeze",),
            ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            "action-receipt-v1",
            ActionReconciliationDefinition(
                "account_freeze_status",
                "account.freeze_state",
                "operation_key",
                "operation_key",
                (),
                "freeze_id",
                WriteRecoveryPolicy("IDEMPOTENT_OPERATION"),
            ),
            profile.ref,
            ActionPreparationDefinition.create(
                tool_id="account_security_state",
                requirement_id="account.current_state",
                readiness_field="status",
                readiness_value="active",
                target_version_field="version",
                target_version_argument="expected_account_version",
            ),
        ),
    )
    requirements = tuple(
        _requirement(tool.authority, tool)
        for tool in tools
    )
    from application.conversation_agent import planning_goal_descriptions
    return CapabilityRegistryBundle(
        tenant_id,
        "customer-service-v1",
        agents,
        skills,
        flows,
        actions,
        requirements,
        tools,
        (profile,),
        planning_shortcuts=tuple(planning_goal_descriptions()),
    )


def _agent(agent_id, tools, skills, verification_profile):
    descriptions = {
        "general": "Resolve general ecommerce service objectives using available evidence.",
        "product_technical": "Resolve product questions using catalog, media and knowledge evidence; do not assume a product category.",
        "order_logistics": "Resolve order and logistics questions using current business records.",
        "billing_refund": "Resolve billing and after-sales requests using current records and policy evidence; distinguish returns, exchanges and refunds and only perform available actions.",
        "account_security": "Assess account security concerns; distinguish user reports from verified facts.",
        "human_service": "Resolve support case objectives using verified case records.",
    }
    principals = {
        "general": "general", "product_technical": "technical",
        "order_logistics": "general", "billing_refund": "billing",
        "account_security": "account_security", "human_service": "escalation",
    }
    return AgentDefinition(
        agent_id,
        "v1",
        tools,
        skills,
        f"{agent_id}-model-v1",
        f"{agent_id}-context-v1",
        verification_profile,
        max_parallelism=2,
        description=descriptions[agent_id],
        tool_principal=principals[agent_id],
    )


def _skill(
    skill_id, owner, objective, required_arguments, requirements, tools, risk,
    verification_profile,
):
    return SkillDefinition(
        skill_id, "v1", owner, objective, required_arguments, (), requirements,
        tools, CapabilityEffect.READ, risk, verification_profile,
    )


def _tool(tool_id, authority, risk, verification_profile, *, write=False):
    effect = CapabilityEffect.WRITE if write else CapabilityEffect.READ
    return ToolDefinition(
        tool_id,
        "v1",
        f"{tool_id}-input-v1",
        f"{tool_id}-output-v1",
        effect,
        risk,
        authority,
        verification_profile,
        f"{tool_id}-receipt-v1" if write else "",
        ("tenant_id", "user_id", "conversation_id"),
        ("order_id",) if "refund" in tool_id or "order" in tool_id else (),
    )


def _requirement(requirement_id: str, tool: ToolDefinition):
    effect = RequirementEffect(tool.effect.value)
    return FactRequirement(
        requirement_id,
        tool.authority,
        ("value",),
        None if effect is RequirementEffect.WRITE else 60,
        effect,
        (tool.tool_id,),
        (),
        tool.receipt_schema_version,
        AuthoritySupport.SUPPORTED,
        f"{tool.authority}-owner-v1",
    )
