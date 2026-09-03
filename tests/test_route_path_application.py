"""Eight RouteMode application-boundary E2E fixtures and forbidden calls."""
import asyncio
from types import SimpleNamespace

import pytest

from agents.agent_orchestrator import AgentOrchestrator
from agents.agent_orchestrator import Request as OrchestrationRequest
from agents.orchestration_contracts import AgentType
from application.chat_application import (
    ChatApplication,
    ChatCommand,
    ChatServices,
)
from application.service_episode_tool import build_service_episode_tool
from application.route_decision import RouteMode
from application.route_execution import (
    CandidateOwner,
    RouteComponent,
    RouteExpectedOutcome,
)
from application.route_path_executor import (
    DeterministicGateResult,
    RouteCandidate,
    RoutePathExecutor,
    RoutePathOperations,
)
from application.route_outcomes import HandoffContractDraft, NeedsInputDraft
from core.intent_recognizer import IntentCategory, UrgencyLevel
from mcp.tool_manager import MCPToolManager


CASES = (
    ("你好", IntentCategory.GREETING, 1.0, RouteMode.DIRECT,
     CandidateOwner.RULE_POLICY, RouteExpectedOutcome.COMPLETED),
    ("退款政策和时限", IntentCategory.REFUND, 0.95, RouteMode.KNOWLEDGE_QA,
     CandidateOwner.GROUNDED_ANSWER_GENERATOR, RouteExpectedOutcome.COMPLETED),
    ("我的退款状态", IntentCategory.REFUND, 0.95, RouteMode.AGENT_TASK,
     CandidateOwner.AGENT, RouteExpectedOutcome.COMPLETED),
    ("我的退款进度，一般多久到账", IntentCategory.REFUND, 0.95, RouteMode.MIXED,
     CandidateOwner.MIXED_AUTHORITY_AGENT, RouteExpectedOutcome.COMPLETED),
    ("登录失败而且重复扣款", IntentCategory.TECHNICAL_LOGIN, 0.95,
     RouteMode.MULTI_DOMAIN, CandidateOwner.TASK_GRAPH, RouteExpectedOutcome.COMPLETED),
    ("这个怎么办", IntentCategory.OTHER, 0.2, RouteMode.CLARIFY,
     CandidateOwner.RULE_POLICY, RouteExpectedOutcome.NEEDS_INPUT),
    ("我要人工", IntentCategory.HUMAN_HANDOFF, 1.0, RouteMode.HANDOFF,
     CandidateOwner.HANDOFF_DRAFT, RouteExpectedOutcome.HANDOFF_DRAFT),
    ("今天股票走势", IntentCategory.OTHER, 0.95, RouteMode.OUT_OF_SCOPE,
     CandidateOwner.RULE_POLICY, RouteExpectedOutcome.COMPLETED),
)


def _orchestrator():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        owner: [object()] for owner in (
            AgentType.GENERAL, AgentType.TECHNICAL, AgentType.BILLING,
            AgentType.ACCOUNT_SECURITY, AgentType.ESCALATION,
        )
    }
    return orchestrator


def test_chat_application_agent_path_calls_service_episode_tool_with_identity():
    search_calls = []

    class Search:
        def search(self, **kwargs):
            search_calls.append(kwargs)
            return SimpleNamespace(to_dict=lambda: {
                "status": "OK",
                "hits": [{
                    "episode_id": "case-e401", "episode_revision": "1",
                    "provenance_sha256": "a" * 64,
                }],
                "detail_code": None,
            })

    manager = MCPToolManager(api_key="test-key")
    manager.register(build_service_episode_tool(lambda: Search()))
    observed = []

    async def evaluate(invocation):
        assert invocation.route_decision.mode in {
            RouteMode.AGENT_TASK, RouteMode.MULTI_DOMAIN,
        }
        result = await manager.call(
            "service_episode_search",
            {"query": invocation.command.message, "entity_ids": ["device-1"]},
            dict(invocation.identity_metadata),
        )
        assert result.success is True
        observed.append(result.data)

    services = ChatServices(
        orchestrator=_orchestrator(), memory=object(), answer_verifier=object(),
        ticket_service=object(), response_delivery=object(),
        context_assembler=object(), bundle_registry=object(),
        bundle_resolver=object(), tool_manager=manager,
    )
    app = ChatApplication(services, SimpleNamespace())
    intent_result = SimpleNamespace(
        intent=IntentCategory.TECHNICAL_LOGIN, intent_group="technical",
        urgency=UrgencyLevel.LOW, confidence=0.95,
        entities={"device_id": ["device-1"]}, source_scores={},
        classifier_fingerprint="intent-v1", input_fingerprint="a" * 64,
    )
    plan = asyncio.run(app._plan_route_path(
        command=ChatCommand(
            message="E401 登录失败", user_id="user-1", tenant_id="tenant-1",
        ),
        identity_metadata={"tenant_id": "tenant-1", "user_id": "user-1"},
        bundle=SimpleNamespace(version="bundle-v1"),
        intent_result=intent_result, user_id="user-1", conv_id="conversation-1",
        request_id="request-1", stages=[],
    ))
    asyncio.run(evaluate(plan))

    assert observed[0]["hits"][0]["episode_id"] == "case-e401"
    assert search_calls == [{
        "tenant_id": "tenant-1", "user_id": "user-1",
        "query": "E401 登录失败", "entity_ids": ("device-1",),
        "purpose": "HISTORICAL_EVIDENCE",
        "explicit_time_reference": False,
        "top_k": 5,
    }]


@pytest.mark.parametrize(
    ("message", "intent", "confidence", "expected_mode", "owner", "outcome"),
    CASES,
)
def test_eight_route_modes_execute_expected_path_and_forbid_all_others(
    message, intent, confidence, expected_mode, owner, outcome,
):
    executed = []
    results = []
    contracts = []

    async def evaluate(invocation):
        contract = invocation.execution_contract
        contracts.append(contract)

        async def rule(_contract):
            executed.append("rule")
            payload = None
            if _contract.expected_outcome is RouteExpectedOutcome.NEEDS_INPUT:
                payload = NeedsInputDraft(
                    workflow_run_id="draft-run:r",
                    signal_id="draft-signal:r",
                    kind="user_input",
                    expires_at="draft:not-persisted",
                    interaction_publication_id="draft-publication:r",
                    missing_inputs=_contract.missing_inputs,
                    prompt="请补充客服诉求",
                )
            return RouteCandidate(
                "rule", CandidateOwner.RULE_POLICY, outcome_payload=payload,
            )

        async def retrieve(_contract):
            executed.append("retrieve")
            return "evidence"

        async def grounded(_contract, evidence):
            assert evidence == "evidence"
            executed.append("grounded")
            return RouteCandidate(
                "grounded", CandidateOwner.GROUNDED_ANSWER_GENERATOR,
                evidence_refs=("chunk-1",),
            )

        async def agent(_contract, evidence):
            executed.append("agent")
            assert (evidence is not None) is (expected_mode is RouteMode.MIXED)
            receipts = tuple(component for component in (
                RouteComponent.BUSINESS_TOOL,
                RouteComponent.AGENT_KNOWLEDGE_TOOL,
            ) if component in contract.required_components)
            return RouteCandidate("agent", contract.candidate_owner, receipts)

        async def handoff(_contract):
            executed.append("handoff")
            payload = HandoffContractDraft(
                handoff_id="draft-handoff:r",
                reason_codes=_contract.reason_codes,
                target_queue_or_owner="support:triage",
                problem_summary=message,
                user_goal=message,
                verified_facts=(),
                user_assertions=(message,),
                actions_attempted=(),
                action_receipts=(),
                missing_materials=_contract.missing_inputs,
                media_evidence=(),
                emotion_and_user_request=message,
                commitments_and_sla=(),
                risk=_contract.risk,
                recommended_next_action="人工核验后继续处理",
            )
            return RouteCandidate(
                "draft", CandidateOwner.HANDOFF_DRAFT, outcome_payload=payload,
            )

        async def gate(_contract, _candidate):
            executed.append("gate")
            return DeterministicGateResult(True, False, "PASS")

        async def semantic(*_args):
            raise AssertionError("fixtures have no unresolved semantic ambiguity")

        async def record(*_args):
            executed.append("record")

        result = await RoutePathExecutor().execute(contract, RoutePathOperations(
            rule, retrieve, grounded, agent, handoff, gate, semantic, record,
        ))
        results.append(result)
        return result

    component = object()
    services = ChatServices(
        orchestrator=_orchestrator(), memory=component, answer_verifier=component,
        ticket_service=component, response_delivery=component,
        context_assembler=component, bundle_registry=component,
        bundle_resolver=component,
    )
    app = ChatApplication(services, SimpleNamespace())
    intent_result = SimpleNamespace(
        intent=intent, intent_group=intent.value, urgency=UrgencyLevel.LOW,
        confidence=confidence, entities={}, source_scores={},
        classifier_fingerprint="intent-v1", input_fingerprint="a" * 64,
    )
    stages = []

    plan = asyncio.run(app._plan_route_path(
        command=ChatCommand(message=message, user_id="u"),
        identity_metadata={"tenant_id": "default", "user_id": "u"},
        bundle=SimpleNamespace(version="bundle-v1"), intent_result=intent_result,
        user_id="u", conv_id="c", request_id="r", stages=stages,
    ))
    asyncio.run(evaluate(plan))

    contract_mode = stages[0].detail["mode"]
    assert contract_mode == expected_mode.value
    assert executed[-1] == "record"
    assert ("retrieve" in executed) is (
        expected_mode in {RouteMode.KNOWLEDGE_QA, RouteMode.MIXED}
    )
    assert ("grounded" in executed) is (expected_mode is RouteMode.KNOWLEDGE_QA)
    assert ("agent" in executed) is (
        expected_mode in {RouteMode.AGENT_TASK, RouteMode.MIXED, RouteMode.MULTI_DOMAIN}
    )
    assert ("handoff" in executed) is (expected_mode is RouteMode.HANDOFF)
    assert ("rule" in executed) is (
        expected_mode in {RouteMode.DIRECT, RouteMode.CLARIFY, RouteMode.OUT_OF_SCOPE}
    )
    assert len(results) == 1
    assert results[0].candidate.owner is owner
    assert results[0].expected_outcome is outcome
    assert not set(results[0].invocation_trace) & set(contracts[0].forbidden_components)
    assert set(contracts[0].required_components).issubset(results[0].invocation_trace)


def test_unsupported_account_authority_reaches_application_as_handoff_draft():
    orchestrator = _orchestrator()
    request = OrchestrationRequest(
        message="我的账户资料现在是什么", user_id="u", conv_id="c",
        intent=IntentCategory.ACCOUNT, intent_group="account",
        urgency=UrgencyLevel.LOW, intent_confidence=0.95,
    )

    shape = asyncio.run(orchestrator.classify_request_shape(request))
    route = asyncio.run(orchestrator.decide_route(request, shape))

    assert route.mode is RouteMode.HANDOFF
    assert route.owner_ids == ()
    assert "AUTHORITY_UNSUPPORTED" in route.reason_codes

    from application.authority_policy import AuthorityPolicyRegistry
    from application.coverage_gate import VerificationProfileRegistry
    from application.route_execution import RouteExecutionPolicy

    requirements = AuthorityPolicyRegistry.v1().minimum_requirements(route)
    verification = VerificationProfileRegistry().contract_for(
        route.mode, requirements,
    )
    contract = RouteExecutionPolicy().plan(route, verification)

    async def handoff(_contract):
        return RouteCandidate(
            "draft", CandidateOwner.HANDOFF_DRAFT,
            outcome_payload=HandoffContractDraft(
                handoff_id="draft-handoff:r",
                reason_codes=_contract.reason_codes,
                target_queue_or_owner="support:triage",
                problem_summary=request.message,
                user_goal="人工核验账户状态",
                verified_facts=(), user_assertions=(request.message,),
                actions_attempted=(), action_receipts=(), missing_materials=(),
                media_evidence=(), emotion_and_user_request=request.message,
                commitments_and_sla=(), risk=_contract.risk,
                recommended_next_action="由人工在授权系统中核验账户状态",
            ),
        )

    async def gate(*_args):
        return DeterministicGateResult(True, False, "HANDOFF_DRAFT_COMPLETE")

    async def record(*_args):
        return None

    async def forbidden(*_args):
        raise AssertionError("unsupported authority invoked a non-handoff path")

    result = asyncio.run(RoutePathExecutor().execute(
        contract,
        RoutePathOperations(
            forbidden, forbidden, forbidden, forbidden, handoff,
            gate, forbidden, record,
        ),
    ))

    assert requirements == ()
    assert result.expected_outcome is RouteExpectedOutcome.HANDOFF_DRAFT
    assert result.invocation_trace == (
        RouteComponent.HANDOFF_DRAFT, RouteComponent.TURN_RECORD,
    )
