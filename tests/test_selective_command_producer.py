from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk
from application.route_policy_v2 import (
    ActionDefinition,
    ApprovalPolicy,
    FlowActionRegistry,
    FlowDefinition,
    WorkKind,
)
from application.selective_command_producer import (
    CommandProducerStage,
    EncoderCommandCandidate,
    EncoderCommandDecision,
    EncoderDisposition,
    SelectiveCommandProducer,
)
from application.turn_state import (
    ActiveFlowRef,
    FlowAggregateVersion,
    FlowDefinitionRef,
    PrincipalScope,
    StateAvailability,
    StateSourceStatus,
    TurnStateSnapshot,
)
from application.turn_understanding import (
    CommandKind,
    CommandProposal,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
    fingerprint_message,
)
from evaluation.command_primary_eval import (
    EvalCase,
    UnderstandingDirectRunner,
)
from evaluation.command_primary_eval.selective_adapter import (
    SelectiveUnderstandingAdapter,
)


REFUND_STATUS = FlowDefinitionRef("refund_status", "v1")
EXECUTE_REFUND = FlowDefinitionRef("execute_refund", "v1")


class FakeEncoderArtifact:
    def __init__(self, decisions):
        self.decisions = decisions
        self.calls = []

    async def decide(self, message, state, registry, *, history=()):
        self.calls.append((message, state.fingerprint, registry.fingerprint, history))
        return self.decisions[message]


class FakeLLMProducer:
    def __init__(self, command_by_message):
        self.command_by_message = command_by_message
        self.calls = []

    async def produce(
        self,
        message,
        message_fingerprint,
        state,
        registry,
        candidates,
        *,
        history=(),
        bundle=None,
    ):
        del registry, history, bundle
        self.calls.append((message, candidates))
        kind, flow = self.command_by_message[message]
        source_flow = None
        target_flow = None
        if kind is CommandKind.CONTINUE_FLOW:
            source_flow = next(
                item for item in state.active_flows if item.definition == flow
            )
        elif kind is CommandKind.START_FLOW:
            target_flow = flow
        return UnderstandingResult(
            UnderstandingStatus.RESOLVED,
            (CommandProposal(
                kind=kind,
                source=UnderstandingSource.LLM,
                message_fingerprint=message_fingerprint,
                producer_version="fake-structured-llm-v1",
                evidence_refs=(f"message:{message_fingerprint}",),
                source_flow=source_flow,
                target_flow=target_flow,
            ),),
        )


def _state() -> TurnStateSnapshot:
    principal = PrincipalScope("tenant-1", "user-1", "conversation-1")
    active = ActiveFlowRef(
        REFUND_STATUS,
        "refund-status-instance",
        3,
        principal.fingerprint,
    )
    return TurnStateSnapshot(
        "request-1",
        principal,
        FlowAggregateVersion("flow-state:conversation-1", 5),
        (active,),
        None,
        (),
        (),
        (),
        (StateSourceStatus(
            "flow_state",
            StateAvailability.CURRENT,
            "flow-state-v1",
        ),),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )


def _registry() -> FlowActionRegistry:
    return FlowActionRegistry(
        "tenant-1",
        "flow-registry-v1",
        (
            FlowDefinition(REFUND_STATUS, (CommandKind.CONTINUE_FLOW,)),
            FlowDefinition(EXECUTE_REFUND, (CommandKind.START_FLOW,)),
        ),
        (
            ActionDefinition(
                "knowledge.answer",
                "v1",
                CommandKind.ANSWER_KNOWLEDGE,
                None,
                WorkKind.KNOWLEDGE,
                AgentType.GENERAL,
                TaskEffect.READ_ONLY,
                TaskRisk.LOW,
                ("knowledge.active_source",),
                ("knowledge_search",),
                ApprovalPolicy.USER_COMMAND_SUFFICIENT,
                "answer from grounded product knowledge",
            ),
            ActionDefinition(
                "refund.status.read",
                "v1",
                CommandKind.CONTINUE_FLOW,
                REFUND_STATUS,
                WorkKind.AGENT,
                AgentType.BILLING,
                TaskEffect.READ_ONLY,
                TaskRisk.LOW,
                ("refund.current_state",),
                ("refund_status",),
                ApprovalPolicy.USER_COMMAND_SUFFICIENT,
                "read the current refund status",
            ),
            ActionDefinition(
                "refund.execute",
                "v1",
                CommandKind.START_FLOW,
                EXECUTE_REFUND,
                WorkKind.AGENT,
                AgentType.BILLING,
                TaskEffect.WRITE_REQUIRES_APPROVAL,
                TaskRisk.HIGH,
                ("refund.request_action",),
                ("refund_request_create",),
                ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
                "execute an approved refund",
            ),
        ),
    )


def _decision(
    disposition: EncoderDisposition,
    candidate: EncoderCommandCandidate,
) -> EncoderCommandDecision:
    return EncoderCommandDecision(
        disposition,
        (candidate,),
        candidate.candidate_id if disposition is EncoderDisposition.ACCEPT else None,
        "bge-m3-flow-head-v1",
        "isotonic-calibration-v1",
        "CALIBRATED_ACCEPT" if disposition is EncoderDisposition.ACCEPT else "DEFER",
    )


def test_calibrated_low_risk_candidate_skips_llm() -> None:
    message = "退款通常多久到账？"
    candidate = EncoderCommandCandidate(
        "knowledge-refund-policy",
        CommandKind.ANSWER_KNOWLEDGE,
        None,
        0.997,
    )
    encoder = FakeEncoderArtifact({message: _decision(EncoderDisposition.ACCEPT, candidate)})
    llm = FakeLLMProducer({})

    outcome = asyncio.run(SelectiveCommandProducer(encoder, llm).produce(
        message,
        fingerprint_message(message),
        _state(),
        _registry(),
    ))

    assert outcome.stage is CommandProducerStage.ENCODER
    assert outcome.understanding.commands[0].kind is CommandKind.ANSWER_KNOWLEDGE
    assert outcome.understanding.commands[0].source is UnderstandingSource.ENCODER
    assert llm.calls == []


def test_deferred_candidate_is_resolved_by_structured_llm() -> None:
    message = "继续查这笔退款"
    candidate = EncoderCommandCandidate(
        "refund-status",
        CommandKind.CONTINUE_FLOW,
        REFUND_STATUS,
        0.72,
    )
    encoder = FakeEncoderArtifact({message: _decision(EncoderDisposition.DEFER, candidate)})
    llm = FakeLLMProducer({message: (CommandKind.CONTINUE_FLOW, REFUND_STATUS)})

    outcome = asyncio.run(SelectiveCommandProducer(encoder, llm).produce(
        message,
        fingerprint_message(message),
        _state(),
        _registry(),
    ))

    assert outcome.stage is CommandProducerStage.LLM
    assert outcome.understanding.commands[0].source is UnderstandingSource.LLM
    assert llm.calls[0][1] == (candidate,)


def test_registry_risk_keeps_write_candidate_on_llm_path() -> None:
    message = "执行退款"
    candidate = EncoderCommandCandidate(
        "execute-refund",
        CommandKind.START_FLOW,
        EXECUTE_REFUND,
        0.999,
    )
    encoder = FakeEncoderArtifact({message: _decision(EncoderDisposition.ACCEPT, candidate)})
    llm = FakeLLMProducer({message: (CommandKind.START_FLOW, EXECUTE_REFUND)})

    outcome = asyncio.run(SelectiveCommandProducer(encoder, llm).produce(
        message,
        fingerprint_message(message),
        _state(),
        _registry(),
    ))

    assert outcome.stage is CommandProducerStage.LLM
    assert outcome.understanding.commands[0].kind is CommandKind.START_FLOW
    assert len(llm.calls) == 1


def test_understanding_runner_reports_quality_and_cascade_cost(tmp_path) -> None:
    faq = "退款通常多久到账？"
    continuation = "继续查这笔退款"
    faq_candidate = EncoderCommandCandidate(
        "knowledge-refund-policy",
        CommandKind.ANSWER_KNOWLEDGE,
        None,
        0.997,
    )
    continuation_candidate = EncoderCommandCandidate(
        "refund-status",
        CommandKind.CONTINUE_FLOW,
        REFUND_STATUS,
        0.72,
    )
    encoder = FakeEncoderArtifact({
        faq: _decision(EncoderDisposition.ACCEPT, faq_candidate),
        continuation: _decision(EncoderDisposition.DEFER, continuation_candidate),
    })
    llm = FakeLLMProducer({
        continuation: (CommandKind.CONTINUE_FLOW, REFUND_STATUS),
    })
    producer = SelectiveCommandProducer(encoder, llm)
    state = _state()
    registry = _registry()
    cases = (
        EvalCase("faq-1", faq, {}, {
            "understanding_status": "RESOLVED",
            "producer_stage": "ENCODER",
            "commands": [{"kind": "ANSWER_KNOWLEDGE"}],
            "encoder_candidates": [{"kind": "ANSWER_KNOWLEDGE"}],
        }),
        EvalCase("continuation-1", continuation, {}, {
            "understanding_status": "RESOLVED",
            "producer_stage": "LLM",
            "commands": [{
                "kind": "CONTINUE_FLOW",
                "flow_id": REFUND_STATUS.flow_id,
                "flow_version": REFUND_STATUS.version,
            }],
            "encoder_candidates": [{
                "kind": "CONTINUE_FLOW",
                "flow_id": REFUND_STATUS.flow_id,
                "flow_version": REFUND_STATUS.version,
            }],
        }),
    )
    adapter = SelectiveUnderstandingAdapter(
        producer,
        lambda _case: state,
        lambda _state: registry,
    )
    report = asyncio.run(UnderstandingDirectRunner(adapter).run(
        cases,
        tmp_path,
        run_id="understanding-smoke-run",
        dataset_id="understanding-smoke-v1",
        split="dev",
    ))

    assert report.passed == 2
    assert report.dimensions["trigger"]["rate"] == 1.0
    assert report.dimensions["artifact"]["rate"] == 1.0
    assert report.dimensions["consumption"]["rate"] == 1.0
    assert report.dimensions["cost"]["total_invocations"] == 3
    assert {item.name for item in tmp_path.iterdir()} == {
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    }
