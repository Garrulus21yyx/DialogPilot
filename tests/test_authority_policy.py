"""M2-T02 minimum fact requirements and governed tool manifests."""
from datetime import datetime, timedelta, timezone

import pytest

from application.authority_policy import (
    AuthorityContractError,
    AuthorityPolicyRegistry,
    UnsupportedAuthority,
)
from application.route_decision import (
    ComponentInvocation,
    ComponentStatus,
    RequiredAuthority,
    RouteDecision,
    RouteMode,
    RouteRisk,
)
from mcp.tool_manager import Tool
from mcp.customer_operations_tools import customer_operation_tools
from mcp.customer_support_tools import ticket_tools
from services.customer_operations import CustomerOperationsService
from services.ticket_service import TicketService


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


async def _handler(params, context):
    return {}


def _tool(
    name="order_lookup",
    *,
    authority="order.current_state",
    output_fields=("order_id", "status", "version", "updated_at"),
    manifest_version="tool-manifest-v1",
):
    return Tool(
        name=name,
        description="test",
        handler=_handler,
        schema={"type": "object"},
        authority=authority,
        manifest_version=manifest_version,
        output_schema_version="test-output-v1",
        preconditions=("authenticated_user",),
        idempotency="read_only",
        retry_policy="safe_read_retry",
        typed_outcomes=("OK", "NOT_FOUND"),
        output_fields=output_fields,
    )


def _route(authorities, *, intent="order_status", mode=RouteMode.AGENT_TASK):
    components = tuple(
        ComponentInvocation(
            component, ComponentStatus.SKIPPED, "TEST", "f" * 64, "test-v1"
        )
        for component in ("intent_fusion", "domain_routing", "instance_selection")
    )
    return RouteDecision(
        mode=mode,
        intent=intent,
        confidence=1.0,
        required_authorities=tuple(authorities),
        risk=RouteRisk.MEDIUM,
        reason_codes=("TEST",),
        missing_inputs=(),
        owner_ids=("billing",),
        component_invocations=components,
        policy_version="test-v1",
        input_fingerprint="f" * 64,
    )


def test_planner_can_add_requirements_but_cannot_omit_route_minimum():
    registry = AuthorityPolicyRegistry.v1()
    route = _route((RequiredAuthority.ORDER_STATE,))

    resolved = registry.resolve_requirements(
        route, ("knowledge.active_source",)
    )

    assert [item.requirement_id for item in resolved] == [
        "knowledge.active_source", "order.current_state",
    ]


def test_generated_text_or_knowledge_cannot_satisfy_dynamic_order_state():
    registry = AuthorityPolicyRegistry.v1()
    generated = registry.validate_output(
        "order.current_state",
        tool=_tool(),
        output="订单已发货",
        observed_at=NOW,
        now=NOW,
    )
    knowledge = _tool(
        "knowledge_search",
        authority="knowledge.active_source",
        output_fields=("source_id", "source_revision", "checksum", "content"),
    )
    result = registry.validate_output(
        "order.current_state",
        tool=knowledge,
        output={
            "source_id": "source-1", "source_revision": "rev-1",
            "checksum": "abc", "content": "订单已发货",
        },
        observed_at=NOW,
        now=NOW,
    )

    assert generated.reason_code == "STRUCTURED_TOOL_OUTPUT_REQUIRED"
    assert result.satisfied is False
    assert result.reason_code == "TOOL_NOT_ALLOWED_FOR_REQUIREMENT"


def test_missing_fields_and_stale_observations_fail_closed():
    registry = AuthorityPolicyRegistry.v1()
    tool = _tool()
    missing = registry.validate_output(
        "order.current_state",
        tool=tool,
        output={"order_id": "order-1", "status": "shipped"},
        observed_at=NOW,
        now=NOW,
    )
    stale = registry.validate_output(
        "order.current_state",
        tool=tool,
        output={
            "order_id": "order-1", "status": "shipped",
            "version": 3, "updated_at": NOW.isoformat(),
        },
        observed_at=NOW - timedelta(seconds=61),
        now=NOW,
    )

    assert missing.reason_code == "REQUIRED_FIELDS_MISSING"
    assert missing.missing_fields == ("version", "updated_at")
    assert stale.reason_code == "OBSERVATION_STALE"


def test_unsupported_authority_cannot_be_replaced_by_knowledge():
    registry = AuthorityPolicyRegistry.v1()
    route = _route((RequiredAuthority.ACCOUNT_STATE,), intent="account_status")

    with pytest.raises(UnsupportedAuthority):
        registry.resolve_requirements(route, ("knowledge.active_source",))
    with pytest.raises(UnsupportedAuthority):
        registry.resolve_requirements(
            _route((RequiredAuthority.ORDER_STATE,)),
            ("commitment.current_state", "knowledge.active_source"),
        )


def test_unknown_requirement_and_invalid_manifest_versions_cannot_mint_evidence():
    registry = AuthorityPolicyRegistry.v1()
    with pytest.raises(AuthorityContractError, match="unknown fact requirement"):
        registry.get("invented.current_state")
    with pytest.raises(AuthorityContractError, match="unsupported tool manifest"):
        registry.validate_tool_manifest(_tool(manifest_version="tool-manifest-v99"))
    with pytest.raises(AuthorityContractError, match="omits required fields"):
        registry.validate_tool_manifest(_tool(output_fields=("order_id",)))

    with pytest.raises(AuthorityContractError, match="producer version"):
        registry.authorize_evidence_adapter(
            requirement_id="order.current_state",
            adapter_id="business-tool-evidence-adapter",
            adapter_version="business-tool-evidence-adapter-v1",
            producer_id="order_lookup",
            producer_version="order-view-v99",
        )


def test_fresh_structured_authority_output_satisfies_requirement():
    result = AuthorityPolicyRegistry.v1().validate_output(
        "order.current_state",
        tool=_tool(),
        output={
            "order_id": "order-1", "status": "shipped",
            "version": 3, "updated_at": NOW.isoformat(),
        },
        observed_at=NOW,
        now=NOW,
    )
    assert result.satisfied is True
    assert result.reason_code == "REQUIREMENT_SATISFIED"


def test_all_builtin_tool_manifests_pass_the_same_startup_gate(tmp_path):
    knowledge = _tool(
        "knowledge_search", authority="knowledge.active_source",
        output_fields=("source_id", "source_revision", "checksum", "content"),
    )
    memory = _tool(
        "memory_search", authority="memory.prior_event",
        output_fields=("memory_id", "conversation_id", "event_seq", "content"),
    )
    tools = (
        knowledge,
        memory,
        *ticket_tools(TicketService(str(tmp_path / "tickets.db"))),
        *customer_operation_tools(
            CustomerOperationsService(str(tmp_path / "operations.db"))
        ),
    )

    AuthorityPolicyRegistry.v1().validate_tools(tools)
