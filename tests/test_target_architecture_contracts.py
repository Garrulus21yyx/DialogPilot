from dataclasses import replace
from datetime import datetime, timezone

import pytest

from application.agent_result import (
    AgentResult,
    AgentResultContractError,
    AgentResultStatus,
    EvidenceRequest,
    FactRecord,
    FactSourceKind,
    MissingInputSpec,
)
from application.authority_policy import (
    AuthoritySupport,
    FactRequirement,
    RequirementEffect,
)
from application.capability_registry import (
    ActionDefinition,
    ActionPreparationDefinition,
    ActionReconciliationDefinition,
    AgentDefinition,
    ApprovalPolicy,
    CapabilityEffect,
    CapabilityRegistryBundle,
    CapabilityRegistryError,
    CapabilityRisk,
    FlowDefinition,
    SkillDefinition,
    ToolDefinition,
    VerificationProfile,
)
from application.work_item import (
    ArgumentValue,
    ControlMode,
    WorkItem,
    WorkItemContractError,
)


def _registry() -> CapabilityRegistryBundle:
    read_requirement = FactRequirement(
        requirement_id="refund.current_state",
        authority="refund.current_state",
        required_fields=("status",),
        freshness_seconds=60,
        effect=RequirementEffect.READ,
        allowed_tools=("refund_status",),
        forbidden_tools=("refund_request_create",),
        receipt_schema_version="",
        support=AuthoritySupport.SUPPORTED,
        owner_approval="refund-state-owner-v1",
    )
    write_requirement = FactRequirement(
        requirement_id="refund.request_action",
        authority="refund.request_action",
        required_fields=("refund_id", "status"),
        freshness_seconds=None,
        effect=RequirementEffect.WRITE,
        allowed_tools=("refund_request_create",),
        forbidden_tools=(),
        receipt_schema_version="refund-receipt-v1",
        support=AuthoritySupport.SUPPORTED,
        owner_approval="refund-action-owner-v1",
    )
    tools = (
        ToolDefinition(
            "refund_status",
            "v1",
            "refund-status-input-v1",
            "refund-status-output-v1",
            CapabilityEffect.READ,
            CapabilityRisk.MEDIUM,
            "refund.current_state",
            "refund-status:v1",
            inject_identity_fields=("tenant_id", "user_id"),
            concurrency_key_fields=("order_id",),
        ),
        ToolDefinition(
            "refund_request_create",
            "v1",
            "refund-create-input-v1",
            "refund-create-output-v1",
            CapabilityEffect.WRITE,
            CapabilityRisk.HIGH,
            "refund.request_action",
            "refund-status:v1",
            receipt_schema_version="refund-receipt-v1",
            inject_identity_fields=("tenant_id", "user_id"),
            concurrency_key_fields=("order_id",),
        ),
    )
    return CapabilityRegistryBundle(
        tenant_id="tenant-a",
        bundle_version="customer-service-v1",
        agents=(AgentDefinition(
            "billing_refund",
            "v1",
            ("refund_status", "refund_request_create"),
            ("refund_status_summary",),
            "refund-agent-v1",
            "refund-context-v1",
            "refund-status:v1",
            max_parallelism=2,
        ),),
        skills=(SkillDefinition(
            "refund_status_summary",
            "v1",
            "billing_refund",
            "Query and explain the current refund state",
            ("order_id",),
            (),
            ("refund.current_state",),
            ("refund_status",),
            CapabilityEffect.READ,
            CapabilityRisk.MEDIUM,
            "refund-status:v1",
        ),),
        flows=(FlowDefinition(
            "execute_refund",
            "v1",
            "billing_refund",
            ("CHECK_ELIGIBILITY", "CHECK_APPROVAL", "EXECUTE", "RECONCILE", "COMPLETE"),
            "CHECK_ELIGIBILITY",
            ("COMPLETE",),
            ("refund_status", "refund_request_create"),
        ),),
        actions=(ActionDefinition(
            "refund.request.create",
            "v1",
            "billing_refund",
            "execute_refund:v1",
            CapabilityEffect.WRITE,
            CapabilityRisk.HIGH,
            ("refund.request_action",),
            ("refund_request_create",),
            ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            "refund-receipt-v1",
            ActionReconciliationDefinition(
                "refund_status", "refund.current_state",
                "operation_key", "operation_key", ("order_id",), "refund_id",
            ),
            "refund-status:v1",
        ),),
        requirements=(read_requirement, write_requirement),
        tools=tools,
        verification_profiles=(VerificationProfile(
            "refund-status",
            "v1",
            ("authority", "freshness", "required_fields"),
        ),),
    )


def test_registry_rejects_write_tools_as_action_preparation_authority():
    registry = _registry()
    action = replace(
        registry.actions[0],
        preparation=ActionPreparationDefinition.create(
            tool_id="refund_request_create",
            requirement_id="refund.request_action",
            readiness_field="committed",
            readiness_value=True,
            target_version_field="version",
            target_version_argument="expected_version",
        ),
    )

    with pytest.raises(
        CapabilityRegistryError,
        match="preparation tool must be read-only",
    ):
        replace(registry, actions=(action,))


def test_registry_rejects_write_tools_as_reconciliation_authority():
    registry = _registry()
    action = replace(
        registry.actions[0],
        reconciliation=ActionReconciliationDefinition(
            "refund_request_create",
            "refund.request_action",
            "operation_key",
            "operation_key",
            (),
            "refund_id",
        ),
    )

    with pytest.raises(
        CapabilityRegistryError,
        match="reconciliation tool must be read-only",
    ):
        replace(registry, actions=(action,))


def _work(**changes) -> WorkItem:
    values = {
        "work_item_id": "refund-status-1",
        "owner_agent": "billing_refund",
        "objective": "Query refund status",
        "control_mode": ControlMode.DIRECT,
        "allowed_tools": ("refund_status",),
        "allowed_skills": (),
        "arguments": (ArgumentValue.create("order_id", "DP1234"),),
        "requirement_ids": ("refund.current_state",),
        "dependencies": (),
        "effect": CapabilityEffect.READ,
        "risk": CapabilityRisk.MEDIUM,
        "expected_output_schema": "refund-status-output-v1",
        "verification_profile": "refund-status:v1",
        "state_snapshot_version": 7,
        "registry_fingerprint": "capability-bundle:v1:test",
        "timeout_seconds": 8,
        "max_steps": 2,
    }
    values.update(changes)
    return WorkItem(**values)


def test_registry_closes_agent_skill_flow_action_tool_and_requirement_references():
    registry = _registry()

    assert registry.agent("billing_refund").max_parallelism == 2
    assert registry.skill("refund_status_summary").allowed_tool_ids == ("refund_status",)
    assert registry.flow("execute_refund:v1").initial_stage == "CHECK_ELIGIBILITY"
    assert registry.tool("refund_request_create").effect is CapabilityEffect.WRITE
    assert registry.fingerprint.startswith("capability-bundle:v1:")


def test_registry_rejects_skill_tool_outside_owner_allowlist():
    registry = _registry()
    skill = SkillDefinition(
        "bad_skill",
        "v1",
        "billing_refund",
        "Use an undeclared tool",
        (),
        (),
        ("refund.current_state",),
        ("unregistered",),
        CapabilityEffect.READ,
        CapabilityRisk.LOW,
        "refund-status:v1",
    )

    with pytest.raises(CapabilityRegistryError, match="unknown IDs"):
        CapabilityRegistryBundle(
            registry.tenant_id,
            registry.bundle_version,
            registry.agents,
            (*registry.skills, skill),
            registry.flows,
            registry.actions,
            registry.requirements,
            registry.tools,
            registry.verification_profiles,
        )


def test_direct_and_delegated_work_are_distinct_execution_shapes():
    direct = _work()
    delegated = _work(
        work_item_id="refund-explain-1",
        control_mode=ControlMode.DELEGATED,
        allowed_skills=("refund_status_summary",),
        skill_hint="refund_status_summary",
        max_steps=4,
    )

    assert direct.control_mode is ControlMode.DIRECT
    assert delegated.skill_hint == "refund_status_summary"
    assert direct.fingerprint != delegated.fingerprint


def test_direct_work_cannot_hide_agent_or_workflow_planning():
    with pytest.raises(WorkItemContractError, match="forbids skills"):
        _work(allowed_skills=("refund_status_summary",))

    with pytest.raises(WorkItemContractError, match="skill or flow hints"):
        _work(flow_ref="execute_refund:v1")


def test_business_write_requires_workflow_and_all_safety_bindings():
    with pytest.raises(WorkItemContractError, match="WORKFLOW control"):
        _work(effect=CapabilityEffect.WRITE)

    workflow = _work(
        work_item_id="execute-refund-1",
        control_mode=ControlMode.WORKFLOW,
        allowed_tools=("refund_request_create", "refund_status"),
        effect=CapabilityEffect.WRITE,
        risk=CapabilityRisk.HIGH,
        requirement_ids=("refund.request_action",),
        flow_ref="execute_refund:v1",
        operation_key="op-refund-1",
        approval_binding="approval-1:v1",
        target_entity_version="order:DP1234:v7",
        reconciliation=ActionReconciliationDefinition(
            "refund_status", "refund.current_state",
            "operation_key", "operation_key", ("order_id",), "refund_id",
        ),
        aggregate_ref="refund:DP1234",
        action_ref="refund.request.create:v1",
        approval_policy=ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
    )

    assert workflow.control_mode is ControlMode.WORKFLOW
    assert workflow.operation_key == "op-refund-1"


def test_agent_result_separates_missing_user_input_from_external_evidence():
    missing = AgentResult(
        "installation-1",
        "product_technical",
        AgentResultStatus.NEEDS_USER_INPUT,
        "WALL_MATERIAL_REQUIRED",
        "product-agent-v1",
        missing_inputs=(MissingInputSpec(
            "wall_material",
            "installation-1",
            "WALL_MATERIAL_REQUIRED",
            "concrete | brick | drywall | unknown",
            "What is the wall material?",
        ),),
    )
    evidence = AgentResult(
        "installation-1",
        "product_technical",
        AgentResultStatus.NEEDS_EVIDENCE,
        "PRODUCT_MODEL_REQUIRED",
        "product-agent-v1",
        requested_evidence=(EvidenceRequest(
            "product.canonical_model",
            "installation-1",
            ("product_catalog", "media_perception"),
        ),),
    )

    assert missing.missing_inputs and not missing.requested_evidence
    assert evidence.requested_evidence and not evidence.missing_inputs


def test_agent_result_rejects_retry_or_success_state_contradictions():
    with pytest.raises(AgentResultContractError, match="must declare retryable"):
        AgentResult(
            "refund-1",
            "billing_refund",
            AgentResultStatus.RETRYABLE_FAILURE,
            "PROVIDER_TIMEOUT",
            "refund-agent-v1",
        )

    with pytest.raises(AgentResultContractError, match="remain blocked"):
        AgentResult(
            "refund-1",
            "billing_refund",
            AgentResultStatus.SUCCEEDED,
            "DONE",
            "refund-agent-v1",
            requested_evidence=(EvidenceRequest(
                "refund.current_state",
                "refund-1",
                ("refund_status",),
            ),),
        )


def test_fact_requires_versioned_provenance_and_timezone():
    fact = FactRecord(
        "refund:R123",
        "refund.current_state",
        '{"status":"PROCESSING"}',
        FactSourceKind.VERIFIED_STATE,
        "receipt:refund-status-123",
        "refund_status",
        "v2",
        datetime.now(timezone.utc),
    )

    assert fact.source_kind is FactSourceKind.VERIFIED_STATE

    with pytest.raises(AgentResultContractError, match="timezone-aware"):
        FactRecord(
            "refund:R123",
            "refund.current_state",
            '{"status":"PROCESSING"}',
            FactSourceKind.VERIFIED_STATE,
            "receipt:refund-status-123",
            "refund_status",
            "v2",
            datetime.now(),
        )
