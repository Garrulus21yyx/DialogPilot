"""M2-T06 route-specific execution/publication contract invariants."""
import asyncio
from types import SimpleNamespace

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
    agent_route_candidate,
)
from application.route_outcomes import (
    HandoffContractDraft,
    NeedsInputDraft,
    RouteOutcomeContractError,
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
    missing_inputs = ("customer_request",) if mode is RouteMode.CLARIFY else ()
    return RouteDecision(
        mode, "test", 1.0, authorities, RouteRisk.LOW, ("TEST",), missing_inputs, (),
        components, "router-v1", input_fingerprint,
    )


def plan(mode: RouteMode):
    decision = route(mode)
    verification = VerificationProfileRegistry().contract_for(mode, ())
    return RouteExecutionPolicy().plan(decision, verification)


def needs_input_draft(contract):
    return NeedsInputDraft(
        workflow_run_id="draft-run:r",
        signal_id="draft-signal:r",
        kind="user_input",
        expires_at="draft:not-persisted",
        interaction_publication_id="draft-publication:r",
        missing_inputs=contract.missing_inputs,
        prompt="请补充客服诉求",
    )


def handoff_contract_draft(contract):
    return HandoffContractDraft(
        handoff_id="draft-handoff:r",
        reason_codes=contract.reason_codes,
        target_queue_or_owner="support:triage",
        problem_summary="用户要求人工处理退款问题",
        user_goal="由人工核验退款状态",
        verified_facts=("退款单 R-1 已提交",),
        user_assertions=("用户表示已等待三天",),
        actions_attempted=("查询退款状态",),
        action_receipts=("receipt-1",),
        missing_materials=("支付凭证",),
        media_evidence=(),
        emotion_and_user_request="用户焦虑并明确要求人工",
        commitments_and_sla=("尚未作出 SLA 承诺",),
        risk=contract.risk,
        recommended_next_action="人工核验 receipt-1 后联系用户",
    )


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
        payload = (
            needs_input_draft(_contract)
            if _contract.expected_outcome is RouteExpectedOutcome.NEEDS_INPUT
            else None
        )
        return RouteCandidate(
            "candidate", CandidateOwner.RULE_POLICY, outcome_payload=payload,
        )

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
        return RouteCandidate(
            "candidate", CandidateOwner.HANDOFF_DRAFT,
            outcome_payload=handoff_contract_draft(_contract),
        )

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


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [
        (RouteMode.CLARIFY, "INVALID_NEEDS_INPUT_PAYLOAD"),
        (RouteMode.HANDOFF, "INVALID_HANDOFF_DRAFT"),
    ],
)
def test_typed_nonterminal_route_payload_is_required(mode, error_code):
    contract = plan(mode)

    async def candidate_operation(_contract):
        return RouteCandidate("text is insufficient", contract.candidate_owner)

    async def forbidden(*_args):
        raise AssertionError("executor continued past invalid route outcome")

    operations = RoutePathOperations(
        candidate_operation, forbidden, forbidden, forbidden,
        candidate_operation, forbidden, forbidden, forbidden,
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(RoutePathExecutor().execute(contract, operations))

    assert getattr(exc_info.value, "code", None) == error_code


def test_handoff_draft_matches_target_schema_and_cannot_claim_release():
    contract = plan(RouteMode.HANDOFF)
    draft = handoff_contract_draft(contract)

    assert set(draft.__dataclass_fields__) >= {
        "handoff_id", "reason_codes", "target_queue_or_owner",
        "problem_summary", "user_goal", "verified_facts", "user_assertions",
        "actions_attempted", "action_receipts", "missing_materials",
        "media_evidence", "emotion_and_user_request", "commitments_and_sla",
        "risk", "recommended_next_action",
    }
    assert draft.release_status == "draft_only"

    values = {
        field: getattr(draft, field) for field in draft.__dataclass_fields__
    }
    values["release_status"] = "handed_off"
    with pytest.raises(RouteOutcomeContractError):
        HandoffContractDraft(**values)


def test_needs_input_draft_rejects_empty_missing_input_contract():
    with pytest.raises(RouteOutcomeContractError):
        NeedsInputDraft(
            workflow_run_id="run", signal_id="signal", kind="user_input",
            expires_at="later", interaction_publication_id="publication",
            missing_inputs=(), prompt="please clarify",
        )


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


def test_agent_candidate_uses_native_tool_receipts_not_audit_inference():
    contract = plan(RouteMode.AGENT_TASK)
    result = SimpleNamespace(
        response="refund is processing",
        agent_outcomes=[{"tool_receipts": [{
            "call_id": "call-1", "tool_name": "refund_status",
            "status": "success", "authority": "CustomerOperations",
            "output_schema_version": "refund-view-v1",
            "receipt_schema_version": "evidence-receipt-v1",
            "effect_status": "none", "receipt_id": "receipt-1",
        }]}],
        tool_audit=[{"tool_name": "knowledge_search", "status": "success"}],
    )

    candidate = agent_route_candidate(contract, result)

    assert candidate.component_receipts == (RouteComponent.BUSINESS_TOOL,)
    assert candidate.evidence_refs == ("receipt-1",)


def test_untyped_or_failed_tool_result_cannot_satisfy_route_component():
    contract = plan(RouteMode.AGENT_TASK)
    result = SimpleNamespace(response="guess", agent_outcomes=[{
        "tool_receipts": [
            {"tool_name": "refund_status", "status": "success", "authority": ""},
            {
                "tool_name": "refund_status", "status": "error",
                "authority": "CustomerOperations",
                "output_schema_version": "refund-view-v1",
            },
        ],
    }])

    candidate = agent_route_candidate(contract, result)

    assert candidate.component_receipts == ()
    assert candidate.evidence_refs == ()
