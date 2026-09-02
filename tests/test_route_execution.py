"""M2-T06 route-specific execution/publication contract invariants."""
import asyncio

import pytest

from application.coverage_gate import VerificationProfileRegistry
from application.route_decision import (
    ComponentInvocation,
    ComponentStatus,
    RequiredAuthority,
    RouteDecision,
    RouteMode,
    RouteRisk,
)
from application.route_execution import (
    CandidateOwner,
    RouteComponent,
    RouteExecutionPolicy,
    RouteExpectedOutcome,
)
from application.route_path_executor import (
    DeterministicGateResult,
    RouteCandidate,
    RoutePathExecutor,
    RoutePathOperations,
)


SHA = "a" * 64


def route(mode: RouteMode, *, input_fingerprint: str = SHA) -> RouteDecision:
    components = tuple(ComponentInvocation(
        name, ComponentStatus.SKIPPED, "TEST", input_fingerprint, "test-v1",
    ) for name in ("intent_fusion", "domain_routing", "instance_selection"))
    authorities = () if mode in {
        RouteMode.DIRECT, RouteMode.CLARIFY, RouteMode.OUT_OF_SCOPE,
    } else {
        RouteMode.HANDOFF: (RequiredAuthority.HUMAN,),
        RouteMode.KNOWLEDGE_QA: (RequiredAuthority.KNOWLEDGE,),
        RouteMode.AGENT_TASK: (RequiredAuthority.DOMAIN_TOOL,),
        RouteMode.MIXED: (
            RequiredAuthority.KNOWLEDGE, RequiredAuthority.DOMAIN_TOOL,
        ),
        RouteMode.MULTI_DOMAIN: (RequiredAuthority.DOMAIN_TOOL,),
    }[mode]
    return RouteDecision(
        mode, "test", 1.0, authorities, RouteRisk.LOW, ("TEST",), (), (),
        components, "router-v1", input_fingerprint,
    )


def plan(mode: RouteMode):
    decision = route(mode)
    verification = VerificationProfileRegistry().contract_for(mode, ())
    return RouteExecutionPolicy().plan(decision, verification)


@pytest.mark.parametrize("mode", tuple(RouteMode))
def test_all_route_modes_have_exhaustive_disjoint_component_algebra(mode):
    contract = plan(mode)
    sets = (
        set(contract.required_components),
        set(contract.conditional_components),
        set(contract.forbidden_components),
    )

    assert sets[0] | sets[1] | sets[2] == set(RouteComponent)
    assert not sets[0] & sets[1]
    assert not sets[0] & sets[2]
    assert not sets[1] & sets[2]
    assert RouteComponent.TURN_RECORD in contract.required_components
    assert contract.deterministic_gates


@pytest.mark.parametrize("mode", [RouteMode.DIRECT, RouteMode.OUT_OF_SCOPE])
def test_rule_routes_forbid_retrieval_agents_tools_and_verifier(mode):
    contract = plan(mode)

    assert contract.candidate_owner is CandidateOwner.RULE_POLICY
    assert contract.expected_outcome is RouteExpectedOutcome.COMPLETED
    for component in (
        RouteComponent.PRE_ROUTE_RETRIEVER,
        RouteComponent.GROUNDED_ANSWER_GENERATOR,
        RouteComponent.AGENT_ORCHESTRATOR,
        RouteComponent.BUSINESS_TOOL,
        RouteComponent.SEMANTIC_VERIFIER,
    ):
        assert not contract.permits(component)


def test_knowledge_qa_has_one_grounded_candidate_and_no_agent_path():
    contract = plan(RouteMode.KNOWLEDGE_QA)

    assert contract.candidate_owner is CandidateOwner.GROUNDED_ANSWER_GENERATOR
    assert RouteComponent.PRE_ROUTE_RETRIEVER in contract.required_components
    assert RouteComponent.GROUNDED_ANSWER_GENERATOR in contract.required_components
    assert RouteComponent.CITATION_CLAIM_GATE in contract.required_components
    assert not contract.permits(RouteComponent.AGENT_ORCHESTRATOR)
    assert not contract.permits(RouteComponent.BUSINESS_TOOL)


def test_agent_task_does_not_generate_a_pre_route_grounded_answer():
    contract = plan(RouteMode.AGENT_TASK)

    assert contract.candidate_owner is CandidateOwner.AGENT
    assert RouteComponent.AGENT_ORCHESTRATOR in contract.required_components
    assert RouteComponent.AGENT_KNOWLEDGE_TOOL in contract.conditional_components
    assert RouteComponent.BUSINESS_TOOL in contract.required_components
    assert not contract.permits(RouteComponent.PRE_ROUTE_RETRIEVER)
    assert not contract.permits(RouteComponent.GROUNDED_ANSWER_GENERATOR)


def test_mixed_uses_one_authority_aware_candidate_not_two_answers():
    contract = plan(RouteMode.MIXED)

    assert contract.candidate_owner is CandidateOwner.MIXED_AUTHORITY_AGENT
    assert RouteComponent.PRE_ROUTE_RETRIEVER in contract.required_components
    assert RouteComponent.BUSINESS_TOOL in contract.required_components
    assert not contract.permits(RouteComponent.GROUNDED_ANSWER_GENERATOR)
    assert not contract.permits(RouteComponent.CONDITIONAL_SYNTHESIS)


def test_multi_domain_delegates_task_graph_and_conditional_synthesis():
    contract = plan(RouteMode.MULTI_DOMAIN)

    assert contract.candidate_owner is CandidateOwner.TASK_GRAPH
    assert RouteComponent.TASK_GRAPH in contract.required_components
    assert RouteComponent.CONDITIONAL_SYNTHESIS in contract.conditional_components
    assert not contract.permits(RouteComponent.PRE_ROUTE_RETRIEVER)
    assert not contract.permits(RouteComponent.GROUNDED_ANSWER_GENERATOR)


def test_clarify_forbids_tools_and_projects_needs_input():
    contract = plan(RouteMode.CLARIFY)

    assert contract.expected_outcome is RouteExpectedOutcome.NEEDS_INPUT
    assert RouteComponent.MISSING_INPUT_SIGNAL in contract.required_components
    assert not contract.permits(RouteComponent.BUSINESS_TOOL)
    assert not contract.permits(RouteComponent.AGENT_ORCHESTRATOR)


def test_handoff_is_draft_only_until_release_action():
    contract = plan(RouteMode.HANDOFF)

    assert contract.expected_outcome is RouteExpectedOutcome.HANDOFF_DRAFT
    assert RouteComponent.HANDOFF_DRAFT in contract.required_components
    assert RouteComponent.HANDOFF_WRITE in contract.forbidden_components
    assert not contract.permits(RouteComponent.PRE_ROUTE_RETRIEVER)


def test_route_execution_fingerprint_binds_input_and_policy():
    first = RouteExecutionPolicy().plan(
        route(RouteMode.KNOWLEDGE_QA, input_fingerprint="a" * 64),
        VerificationProfileRegistry().contract_for(RouteMode.KNOWLEDGE_QA, ()),
    )
    replay = RouteExecutionPolicy().plan(
        route(RouteMode.KNOWLEDGE_QA, input_fingerprint="a" * 64),
        VerificationProfileRegistry().contract_for(RouteMode.KNOWLEDGE_QA, ()),
    )
    changed = RouteExecutionPolicy().plan(
        route(RouteMode.KNOWLEDGE_QA, input_fingerprint="b" * 64),
        VerificationProfileRegistry().contract_for(RouteMode.KNOWLEDGE_QA, ()),
    )

    assert first.fingerprint == replay.fingerprint
    assert first.fingerprint != changed.fingerprint


@pytest.mark.parametrize("mode", tuple(RouteMode))
def test_route_path_executor_runs_only_the_selected_path(mode):
    contract = plan(mode)
    calls = []

    def candidate(owner, receipts=()):
        return RouteCandidate("candidate", owner, tuple(receipts))

    async def rule(_contract):
        calls.append("rule")
        return candidate(CandidateOwner.RULE_POLICY)

    async def retrieve(_contract):
        calls.append("retrieve")
        return {"evidence": "knowledge"}

    async def grounded(_contract, evidence):
        calls.append("grounded")
        assert evidence == {"evidence": "knowledge"}
        return candidate(CandidateOwner.GROUNDED_ANSWER_GENERATOR)

    async def agent(_contract, evidence):
        calls.append("agent")
        if mode is RouteMode.MIXED:
            assert evidence == {"evidence": "knowledge"}
        else:
            assert evidence is None
        receipts = tuple(component for component in (
            RouteComponent.BUSINESS_TOOL,
            RouteComponent.AGENT_KNOWLEDGE_TOOL,
        ) if component in contract.required_components)
        return candidate(contract.candidate_owner, receipts)

    async def handoff(_contract):
        calls.append("handoff_draft")
        return candidate(CandidateOwner.HANDOFF_DRAFT)

    async def gate(_contract, _candidate):
        calls.append("deterministic_gate")
        return DeterministicGateResult(True, False, "GATES_PASSED")

    async def semantic(_contract, _candidate):
        calls.append("semantic_verifier")
        return True

    async def record(_contract, _candidate, publishable):
        calls.append("record_turn")
        assert publishable is True

    result = asyncio.run(RoutePathExecutor().execute(
        contract,
        RoutePathOperations(rule, retrieve, grounded, agent, handoff, gate, semantic, record),
    ))

    assert result.publishable is True
    assert result.candidate.owner is contract.candidate_owner
    assert "deterministic_gate" in calls
    assert calls[-1] == "record_turn"
    assert "semantic_verifier" not in calls
    assert not set(result.invocation_trace) & set(contract.forbidden_components)
    assert set(contract.required_components).issubset(result.invocation_trace)
    if mode is RouteMode.KNOWLEDGE_QA:
        assert calls[:2] == ["retrieve", "grounded"]
        assert "agent" not in calls
    if mode is RouteMode.AGENT_TASK:
        assert calls[0] == "agent"
        assert "retrieve" not in calls
    if mode is RouteMode.MIXED:
        assert calls[:2] == ["retrieve", "agent"]
        assert "grounded" not in calls
    if mode is RouteMode.MULTI_DOMAIN:
        assert calls[0] == "agent"
        assert RouteComponent.TASK_GRAPH in result.invocation_trace
    if mode is RouteMode.HANDOFF:
        assert calls[0] == "handoff_draft"
        assert RouteComponent.HANDOFF_WRITE not in result.invocation_trace


def test_semantic_verifier_runs_once_only_after_deterministic_gates_request_it():
    contract = plan(RouteMode.KNOWLEDGE_QA)
    calls = []

    async def retrieve(_contract):
        return "evidence"

    async def grounded(_contract, _evidence):
        return RouteCandidate("answer", CandidateOwner.GROUNDED_ANSWER_GENERATOR)

    async def gate(_contract, _candidate):
        calls.append("gate")
        return DeterministicGateResult(True, True)

    async def semantic(_contract, _candidate):
        calls.append("semantic")
        return True

    async def record(*_args):
        calls.append("record")

    async def forbidden(*_args):
        raise AssertionError("wrong route operation invoked")

    result = asyncio.run(RoutePathExecutor().execute(
        contract,
        RoutePathOperations(
            forbidden, retrieve, grounded, forbidden, forbidden,
            gate, semantic, record,
        ),
    ))

    assert calls == ["gate", "semantic", "record"]
    assert result.invocation_trace.count(RouteComponent.SEMANTIC_VERIFIER) == 1
