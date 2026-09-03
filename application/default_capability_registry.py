"""Executable customer-service capability bundle for Target Architecture v1."""
from __future__ import annotations

from application.authority_policy import AuthoritySupport, FactRequirement, RequirementEffect
from application.capability_registry import (
    ActionDefinition,
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
        _tool("media_read", "media.visible_text", CapabilityRisk.LOW, profile.ref),
        _tool("catalog_search", "product.canonical_model", CapabilityRisk.LOW, profile.ref),
        _tool("order_lookup", "order.current_state", CapabilityRisk.MEDIUM, profile.ref),
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
    )
    skills = (
        _skill(
            "general_qa", "general", "Answer a supported customer-service FAQ",
            ("question",), ("knowledge.active_source",), ("knowledge_search",),
            CapabilityRisk.LOW, profile.ref,
        ),
        _skill(
            "product_identification", "product_technical",
            "Identify a canonical product from media and catalog evidence",
            ("asset_id",), ("product.canonical_model",),
            ("media_read", "catalog_search"), CapabilityRisk.MEDIUM, profile.ref,
        ),
        _skill(
            "product_specification_qa", "product_technical",
            "Answer product specification and installation questions",
            ("product_id",), ("knowledge.active_source",), ("knowledge_search",),
            CapabilityRisk.LOW, profile.ref,
        ),
        _skill(
            "refund_status_summary", "billing_refund", "Query current refund state",
            ("order_id",), ("refund.current_state",), ("refund_status",),
            CapabilityRisk.MEDIUM, profile.ref,
        ),
        _skill(
            "refund_policy_qa", "billing_refund", "Answer refund policy questions",
            ("question",), ("knowledge.active_source",), ("knowledge_search",),
            CapabilityRisk.LOW, profile.ref,
        ),
        _skill(
            "invoice_qa", "billing_refund", "Answer invoice policy questions",
            ("question",), ("knowledge.active_source",), ("knowledge_search",),
            CapabilityRisk.LOW, profile.ref,
        ),
    )
    agents = (
        _agent("general", ("knowledge_search",), ("general_qa",), profile.ref),
        _agent(
            "product_technical",
            ("media_read", "catalog_search", "knowledge_search"),
            ("product_identification", "product_specification_qa"),
            profile.ref,
        ),
        _agent("order_logistics", ("order_lookup",), (), profile.ref),
        _agent(
            "billing_refund",
            (
                "knowledge_search", "order_lookup", "refund_status",
                "refund_eligibility_check", "refund_request_create",
            ),
            ("refund_status_summary", "refund_policy_qa", "invoice_qa"),
            profile.ref,
        ),
        _agent("account_security", (), (), profile.ref),
        _agent("human_service", ("support_ticket_create",), (), profile.ref),
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
            "human_handoff", "v1", "human_service",
            ("PREPARE", "CHECK_POLICY", "CREATE_TICKET", "COMPLETE", "CANCELLED"),
            "PREPARE", ("COMPLETE", "CANCELLED"), ("support_ticket_create",),
        ),
    )
    actions = (
        ActionDefinition(
            "refund.request.create", "v1", "billing_refund", "execute_refund:v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH, ("refund.request_action",),
            ("refund_request_create",), ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            "action-receipt-v1", "refund-status-by-operation-v1", profile.ref,
        ),
        ActionDefinition(
            "support.handoff.create", "v1", "human_service", "human_handoff:v1",
            CapabilityEffect.WRITE, CapabilityRisk.HIGH, ("support.handoff_action",),
            ("support_ticket_create",), ApprovalPolicy.USER_COMMAND_SUFFICIENT,
            "action-receipt-v1", "ticket-by-operation-v1", profile.ref,
        ),
    )
    requirements = tuple(
        _requirement(tool.authority, tool)
        for tool in tools
    )
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
    )


def _agent(agent_id, tools, skills, verification_profile):
    return AgentDefinition(
        agent_id,
        "v1",
        tools,
        skills,
        f"{agent_id}-model-v1",
        f"{agent_id}-context-v1",
        verification_profile,
        max_parallelism=2,
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
