"""Native business schemas survive planning, compilation and governed execution."""
import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import ValidationError

from application.authority_policy import FactRequirement, RequirementEffect, AuthoritySupport
from application.capability_registry import (
    AgentDefinition, ToolDefinition, CapabilityEffect, CapabilityRisk,
    CapabilityRegistryBundle, VerificationProfile,
)
from application.conversation_actions import planning_actions, action_proposal
from application.conversation_agent import ConversationAgent
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.turn_planning import RoutePolicy, TurnPlanCompiler, ProposalDisposition
from core.identity import IdentityFactory
from infrastructure.conversation_tool_catalog import ConversationToolCatalog
from infrastructure.target_tool_execution import TargetToolExecutor
from mcp.tool_manager import MCPToolManager, Tool
from tests.test_conversation_actions import calls
from tests.test_conversation_agent import _state
from tests.test_target_framework_agent import _context


SCHEMA = {"type": "object", "additionalProperties": False,
          "$defs": {"text": {"type": "string", "minLength": 1}},
          "properties": {"goal_id": {"$ref": "#/$defs/text"},
                         "depends_on": {"type": "string"}},
          "required": ["goal_id", "depends_on"]}


def fixture(name="find_customer", owners=("retail",)):
    profile = "read:v1"
    authority = "customer.record"
    registry = CapabilityRegistryBundle("tenant-a", "read-tests",
        tuple(AgentDefinition(owner, "v1", (name,), (), "worker", "context", profile,
                              tool_principal="reader") for owner in owners), (), (), (),
        (FactRequirement(authority, authority, (), None, RequirementEffect.READ,
                         (name,), (), "", AuthoritySupport.SUPPORTED, "test"),),
        (ToolDefinition(name, "v1", "input-v1", "output-v1", CapabilityEffect.READ,
                        CapabilityRisk.LOW, authority, profile),),
        (VerificationProfile("read", "v1", ("authority",)),))
    tools = MCPToolManager("test-key", model="test-model")
    observed = []

    async def handler(params, context):
        observed.append(deepcopy(params))
        return {"customer_id": "customer-1"}

    tools.register(Tool(name, "Read customer identity", handler, deepcopy(SCHEMA),
                        allowed_agents=("reader",), authority=authority))
    catalog = ConversationToolCatalog(tools)
    payload = {"supported_goals": ["atomic_read", "delegate_task"],
               "domain_capabilities": [{"agent_id": owner} for owner in owners],
               "atomic_reads": catalog(registry, _state())}
    return registry, tools, catalog, payload, observed


@pytest.mark.parametrize("name", ["find_customer", "lookup_unseen_record", "review_action"])
def test_exact_schema_and_business_metadata_named_arguments_reach_runtime(name):
    registry, tools, catalog, payload, observed = fixture(name)
    actions = planning_actions(payload)
    read = next(a for a in actions if a.kind == "atomic_read")
    assert read.schema == SCHEMA
    args = {"goal_id": "real-business-value", "depends_on": "real-business-value"}
    raw = action_proposal(actions, calls((read.name, args)), "")

    class Provider:
        async def plan(self, payload):
            return raw

    state = _state()
    observations = TurnObservations("Find the customer record")
    proposal = asyncio.run(ConversationAgent(Provider(), tool_catalog=catalog).plan(
        observations, state, DeterministicResolver().resolve(observations, state), registry))
    assert proposal.disposition is ProposalDisposition.RESOLVED
    identity = IdentityFactory(lambda: "read-test").create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a", request_id="read")
    plan = TurnPlanCompiler().compile(RoutePolicy().accept(proposal, state, registry), state, registry, identity)
    item, = plan.work.items
    result = asyncio.run(TargetToolExecutor(tools, registry=registry)(_context(item)))
    assert observed == [args]
    assert result.status.value == "SUCCEEDED"
    assert result.facts[0].requirement_id == "customer.record"


def test_shared_tool_has_distinct_host_bound_owners():
    _, _, _, payload, _ = fixture(owners=("retail", "orders"))
    reads = [a for a in planning_actions(payload) if a.kind == "atomic_read"]
    assert len({a.name for a in reads}) == 2
    assert {a.bound["target_agent"] for a in reads} == {"retail", "orders"}
    assert all(a.schema == SCHEMA for a in reads)


def test_read_dependency_metadata_does_not_rewrite_business_arguments():
    _, _, _, payload, _ = fixture()
    actions = planning_actions(payload)
    read = next(a for a in actions if a.kind == "atomic_read")
    args = {"goal_id": "business", "depends_on": "business"}
    raw = action_proposal(actions, calls(
        (read.name, args),
        ("bind_read_goals", {"bindings": [{"atomic_call": 1, "goal_id": "identity"}]}),
        ("delegate_task", {"target_agent": "retail", "objective": "Complete the requested return",
                           "allow_action_proposals": True, "depends_on": ["identity"]}),
    ), "")
    assert raw["goals"][0]["arguments"] == args
    assert raw["goals"][0]["goal_id"] == "identity"
    assert raw["goals"][1]["depends_on"] == ["identity"]
    assert len(raw["goals"]) == 2


@pytest.mark.parametrize("bindings", [
    [{"atomic_call": 2, "goal_id": "x"}],
    [{"atomic_call": 1, "goal_id": "x"}, {"atomic_call": 1, "goal_id": "y"}],
])
def test_invalid_read_associations_are_rejected(bindings):
    _, _, _, payload, observed = fixture()
    with pytest.raises(ValueError, match="planning_invalid_read_binding"):
        action_proposal(planning_actions(payload), calls(
            ("find_customer", {"goal_id": "x", "depends_on": "y"}),
            ("bind_read_goals", {"bindings": bindings})), "")
    assert not observed


def test_original_schema_constraints_are_not_lost():
    _, _, _, payload, observed = fixture()
    with pytest.raises(ValidationError):
        action_proposal(planning_actions(payload), calls(
            ("find_customer", {"goal_id": "", "depends_on": "y"})), "")
    assert not observed


def test_sdk_provider_receives_and_converts_the_native_read():
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from tests.test_conversation_actions import provider
    _, _, _, payload, observed = fixture()
    payload["message"] = "Find customer"
    action = next(a for a in planning_actions(payload) if a.kind == "atomic_read")
    assert convert_to_openai_tool(action.tool())["function"]["parameters"] == SCHEMA
    args = {"goal_id": "business", "depends_on": "business"}
    sdk, models = provider((action.name, args))
    raw = asyncio.run(sdk.plan(payload))
    assert raw["goals"][0]["arguments"] == args
    assert raw["goals"][0]["tool_id"] == "find_customer"
    assert all(action.name in model.bound_tool_names for role, model in models.items()
               if role.value == "intent")
    assert not observed  # Planning converts proposals; it does not execute them.


@pytest.mark.parametrize("arguments", [{}, [], None, "bad"])
def test_invalid_provider_arguments_have_typed_outcome(arguments):
    registry, tools, catalog, _, observed = fixture()
    if arguments != {}:
        tools.tools_for_agent("reader")[0].schema = {}

    class Provider:
        async def plan(self, payload):
            return {"status": "resolved", "goals": [{"kind": "atomic_read",
                "target_agent": "retail", "tool_id": "find_customer", "arguments": arguments}]}

    state, message = _state(), TurnObservations("Find customer")
    result = asyncio.run(ConversationAgent(Provider(), tool_catalog=catalog).plan(
        message, state, DeterministicResolver().resolve(message, state), registry))
    assert result.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT
    assert not observed


@pytest.mark.parametrize("mismatch", ["effect", "authority", "requirement", "reserved", "schema_type"])
def test_inconsistent_catalog_is_configuration_failure(mismatch):
    from application.turn_planning import PlanningInvariantError
    registry, tools, catalog, _, observed = fixture()
    tool = tools.tools_for_agent("reader")[0]
    if mismatch == "effect":
        tool.read_only = False
    elif mismatch == "authority":
        tool.authority = "other.authority"
    elif mismatch == "requirement":
        registry = replace(registry, requirements=(replace(registry.requirements[0], authority="other"),))
    elif mismatch == "schema_type":
        tool.schema = True
    else:
        tool.schema = {"type": "object", "properties": {"approved": {"type": "boolean"}}}

    class Provider:
        async def plan(self, payload):
            pytest.fail("An inconsistent catalog must fail before model invocation")

    state, message = _state(), TurnObservations("Find customer")
    with pytest.raises(PlanningInvariantError):
        asyncio.run(ConversationAgent(Provider(), tool_catalog=catalog).plan(
            message, state, DeterministicResolver().resolve(message, state), registry))
    assert not observed


def test_dynamic_schema_uses_turn_filter_contract():
    from application.target_conversation_manager import TargetTurnContext
    registry, tools, catalog, _, _ = fixture()
    tool = tools.tools_for_agent("reader")[0]
    seen = []
    def schema(context):
        seen.append(context)
        return SCHEMA
    tool.schema_factory = schema
    context = TargetTurnContext(knowledge_filter_contract={"test_scope": "known"})
    assert catalog(registry, _state(), context)[0]["input_schema"] == SCHEMA
    assert seen[0]["knowledge_filter_contract"] == context.knowledge_filter_contract


@pytest.mark.parametrize("invalid", [None, "unknown", "duplicate", "cycle"])
def test_read_to_dependent_goal_compiles_and_delivers_facts(invalid):
    from application.agent_result import AgentResult, AgentResultStatus
    from application.orchestration_runtime import OrchestrationRuntime
    from application.work_item import WorkItemContractError
    registry, tools, catalog, payload, observed = fixture()
    dependent_id = "identity" if invalid == "duplicate" else "finish"
    binding = {"atomic_call": 1, "goal_id": "identity"}
    if invalid == "cycle":
        binding["depends_on"] = ["finish"]
    raw = action_proposal(planning_actions(payload), calls(
        ("find_customer", {"goal_id": "business", "depends_on": "business"}),
        ("bind_read_goals", {"bindings": [binding]}),
        ("delegate_task", {"target_agent": "retail", "objective": "Answer the full customer question",
            "allow_action_proposals": False, "goal_id": dependent_id,
            "depends_on": ["unknown" if invalid == "unknown" else "identity"]})), "")

    class Provider:
        async def plan(self, payload):
            return raw

    state, message = _state(), TurnObservations("Find the customer and answer the account question")
    proposal = asyncio.run(ConversationAgent(Provider(), tool_catalog=catalog).plan(
        message, state, DeterministicResolver().resolve(message, state), registry))
    identity = IdentityFactory(lambda: "dependency").create_invocation(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a", request_id="dependency")
    if invalid in {"unknown", "duplicate"}:
        assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT
        assert not observed
        return
    if invalid == "cycle":
        with pytest.raises(WorkItemContractError, match="dependency cycle"):
            accepted = RoutePolicy().accept(proposal, state, registry)
            TurnPlanCompiler().compile(accepted, state, registry, identity)
        assert not observed
        return
    plan = TurnPlanCompiler().compile(RoutePolicy().accept(proposal, state, registry), state, registry, identity)
    consumed = []

    async def worker(context):
        assert len(observed) == 1
        assert context.work_item.objective == "Answer the full customer question"
        assert context.dependency_results[0].facts[0] in context.verified_facts
        consumed.append(context.dependency_results[0].facts[0])
        return AgentResult(context.work_item.work_item_id, "retail", AgentResultStatus.SUCCEEDED,
                           "FIXTURE_COMPLETE", "fixture-v1")

    runtime = OrchestrationRuntime(direct_executor=TargetToolExecutor(tools, registry=registry),
                                   domain_workers={"retail": worker})
    asyncio.run(runtime.execute(plan.work, current_message=message.raw_text,
        trusted_context={"tenant_id": "tenant-a", "user_id": "user-a", "conversation_id": "conversation-a"}))
    assert len(consumed) == 1
