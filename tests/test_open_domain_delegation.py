"""Registration, not business-goal branches, defines open read delegation."""
import asyncio
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator, ValidationError
from langchain_core.messages import AIMessage

from application.capability_registry import CapabilityEffect
from application.conversation_agent import ConversationAgent, planning_output_schema
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.turn_planning import RoutePolicy, TurnPlanCompiler, ProposalDisposition
from core.identity import IdentityFactory
from infrastructure.target_framework_agent import TargetFrameworkAgent
from infrastructure.target_framework_agent import _adapt_framework_result
from mcp.tool_manager import ToolResult
from tests.test_conversation_agent import Provider, _state
from tests.test_target_framework_agent import ScriptedToolModel, _manager, _context, _item


def _proposal(owner):
    return {"status": "resolved", "goals": [{
        "kind": "delegate_task", "target_agent": owner,
        "objective": "Investigate the customer's product question using available records.",
    }]}


@pytest.mark.parametrize("owner", ["catalog_advisor", "merchant_support", "after_sales"])
def test_registered_domain_plans_executes_and_preserves_evidence(owner):
    registry = build_default_capability_registry("tenant-a")
    registered = replace(
        registry.agent("product_technical"), agent_id=owner,
        allowed_tool_ids=("catalog_search",), allowed_skill_ids=(),
        tool_principal=owner, description="Investigate catalog questions.",
    )
    registry = replace(registry, agents=(*registry.agents, registered))
    provider = Provider(_proposal(owner))
    state = _state()
    observations = TurnObservations("Please investigate this product.")
    proposal = asyncio.run(ConversationAgent(provider).plan(
        observations, state, DeterministicResolver().resolve(observations, state), registry,
    ))
    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert provider.calls[0]["domain_capabilities"][-1]["agent_id"] == owner
    validated = RoutePolicy().accept(proposal, state, registry)
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
        request_id="request-a",
    )
    plan = TurnPlanCompiler().compile(validated, state, registry, identity)
    item, = plan.work.items
    assert item.allowed_tools == ("catalog_search",)
    assert item.requirement_ids == ()
    calls = []
    manager = _manager(calls, allowed_agents=(owner,))
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "catalog_search", "args": {"query": "product"}, "id": "lookup-1",
        }]),
        AIMessage(content="The catalog identifies the product as PX-200."),
    ])
    result = asyncio.run(TargetFrameworkAgent(
        model, manager, registry=registry, system_prompt=registered.description,
    )(_context(item)))
    assert result.status.value == "SUCCEEDED"
    assert result.facts[0].requirement_id == "product.canonical_model"
    assert result.facts[0].source_ref == "lookup-1"
    assert calls[0][1]["agent_type"] == owner


def test_open_delegation_uses_read_envelope_without_inventing_write_authority():
    registry = build_default_capability_registry("tenant-a")
    state = _state()
    observations = TurnObservations("Investigate my after-sales options.")
    proposal = asyncio.run(ConversationAgent(Provider(_proposal("billing_refund"))).plan(
        observations, state, DeterministicResolver().resolve(observations, state), registry,
    ))
    validated = RoutePolicy().accept(proposal, state, registry)
    command, = validated.commands
    assert command.allowed_tools
    assert all(registry.tool(tool).effect is CapabilityEffect.READ for tool in command.allowed_tools)


def test_injected_registry_owns_shortcuts_and_execution_budget():
    registry = build_default_capability_registry("tenant-a")
    owner = replace(registry.agent("general"), timeout_seconds=90, max_model_calls=12)
    registry = replace(registry, planning_shortcuts=(),
                       agents=tuple(owner if agent.agent_id == owner.agent_id else agent
                                    for agent in registry.agents))
    state = _state()
    observations = TurnObservations("Help with the available environment.")
    provider = Provider(_proposal("general"))
    proposal = asyncio.run(ConversationAgent(provider).plan(
        observations, state, DeterministicResolver().resolve(observations, state), registry))
    assert provider.calls[0]["supported_goals"] == ["cancel_active_work", "delegate_task"]
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a", request_id="budget")
    plan = TurnPlanCompiler().compile(RoutePolicy().accept(proposal, state, registry),
                                      state, registry, identity)
    assert plan.work.items[0].timeout_seconds == 90
    assert plan.work.items[0].max_steps == 12
    unsupported = Provider({"status": "resolved", "goals": [{"kind": "general_qa", "resolved_query": "Help"}]})
    rejected = asyncio.run(ConversationAgent(unsupported).plan(
        observations, state, DeterministicResolver().resolve(observations, state), registry))
    assert rejected.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


def test_unregistered_domain_is_invalid_provider_output():
    registry = build_default_capability_registry("tenant-a")
    state = _state()
    observations = TurnObservations("Help.")
    proposal = asyncio.run(ConversationAgent(Provider(_proposal("invented"))).plan(
        observations, state, DeterministicResolver().resolve(observations, state), registry,
    ))
    assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


@pytest.mark.parametrize("missing", ["target_agent", "objective"])
def test_delegation_wire_contract_requires_domain_and_objective(missing):
    value = _proposal("general")
    del value["goals"][0][missing]
    with pytest.raises(ValidationError):
        Draft202012Validator(planning_output_schema()).validate(value)


@pytest.mark.parametrize("status", ["error", "timeout"])
def test_open_goal_preserves_tool_failure_without_predeclared_requirements(status):
    item = replace(_item(), requirement_ids=())
    result = _adapt_framework_result(
        _context(item),
        (ToolResult(False, None, "catalog_search", status=status),),
        "test", allowed_authorities={"catalog_search": "product.canonical_model"},
    )
    assert result.status.value == "RETRYABLE_FAILURE"
    assert not result.facts


def test_open_goal_does_not_promote_a_tool_to_another_fact_authority():
    item = replace(_item(), requirement_ids=())
    result = _adapt_framework_result(
        _context(item),
        (ToolResult(True, {"status": "paid"}, "catalog_search",
                    authority="refund.current_state", call_id="read-1"),),
        "test", allowed_authorities={"catalog_search": "product.canonical_model"},
    )
    assert not result.facts
    assert result.status.value == "TERMINAL_FAILURE"
