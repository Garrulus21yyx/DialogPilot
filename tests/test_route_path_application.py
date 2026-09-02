"""Eight RouteMode application-boundary E2E fixtures and forbidden calls."""
import asyncio
from types import SimpleNamespace

import pytest

from agents.agent_orchestrator import AgentOrchestrator
from agents.orchestration_contracts import AgentType
from application.chat_application import (
    ChatApplication,
    ChatCommand,
    ChatServices,
)
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
        rollout_manager=component, route_execution_mode="evaluation",
    )
    app = ChatApplication(services, SimpleNamespace(evaluate_route_path=evaluate))
    intent_result = SimpleNamespace(
        intent=intent, intent_group=intent.value, urgency=UrgencyLevel.LOW,
        confidence=confidence, entities={}, source_scores={},
        classifier_fingerprint="intent-v1", input_fingerprint="a" * 64,
    )
    stages = []

    asyncio.run(app._dispatch_route_path_if_enabled(
        command=ChatCommand(message=message, user_id="u"),
        identity_metadata={"tenant_id": "default", "user_id": "u"},
        bundle=SimpleNamespace(version="bundle-v1"), intent_result=intent_result,
        user_id="u", conv_id="c", request_id="r", stages=stages,
    ))

    contract_mode = stages[0].detail["mode"]
    assert contract_mode == expected_mode.value
    assert stages[0].detail["execution_mode"] == "evaluation"
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
