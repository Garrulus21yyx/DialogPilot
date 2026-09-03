from __future__ import annotations

import asyncio
from types import SimpleNamespace

from application.active_case import (
    ActiveCaseContextView,
    ActiveCaseProjection,
    ActiveCaseSelection,
    ActiveCaseState,
)
from application.chat_application import (
    ChatApplication,
    ChatCommand,
    ChatOperations,
    ChatServices,
    Completed,
)
from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import knowledge_flow_registry
from application.flow_state import FlowStateAggregate
from application.route_policy_v2 import RoutePolicy
from application.turn_plan import TurnPlanCompiler
from application.turn_understanding import (
    CommandKind,
    CommandProposal,
    PendingSlotResolver,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
)
from application.turn_state import ActiveFlowRef, FlowBinding, FlowDefinitionRef
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)


class KnowledgeSemantic:
    def __init__(self, events):
        self.events = events

    async def understand(
        self, message, message_fingerprint, state, registry, **_context,
    ):
        self.events.append("understanding")
        assert state.active_case_refs == ()
        assert state.active_flows[0].definition.flow_id == "refund_status"
        assert state.active_flows[0].bindings[0].value == "DP1234"
        assert registry.tenant_id == "tenant-1"
        return UnderstandingResult(
            UnderstandingStatus.RESOLVED,
            (CommandProposal(
                CommandKind.ANSWER_KNOWLEDGE,
                UnderstandingSource.LLM,
                message_fingerprint,
                "command-router-test-v1",
                (f"message:{message_fingerprint}",),
            ),),
        )


def test_knowledge_primary_runs_through_chat_application_without_legacy_intent():
    events = []

    class Orchestrator:
        async def recognize_intent(self, *_args, **_kwargs):
            raise AssertionError("primary route must not call legacy Intent")

        async def run(self, *_args, **_kwargs):
            raise AssertionError("grounded Knowledge route must not run an Agent")

    class MemoryContext:
        recent_messages = []
        retrieval_hits = []

        @staticmethod
        def to_sections():
            return []

    class Memory:
        async def get_context(self, *_args, **_kwargs):
            events.append("state")
            return MemoryContext()

        async def add_messages(self, *_args, **_kwargs):
            events.append("memory_write")

    class FlowState:
        @staticmethod
        def load(principal):
            events.append("flow_state")
            return FlowStateAggregate.empty(
                principal,
                deletion_epoch=0,
            ).next(
                active_flows=(ActiveFlowRef(
                    FlowDefinitionRef("refund_status", "v1"),
                    "refund-status-1",
                    1,
                    principal.fingerprint,
                    (FlowBinding.create("order_id", "DP1234"),),
                ),),
                pending_slot=None,
            )

    class ContextAssembler:
        @staticmethod
        def assemble(**_kwargs):
            return SimpleNamespace(system_context="")

    class Delivery:
        @staticmethod
        def select_response(**kwargs):
            events.append("delivery")
            assert kwargs["response_text"] == "退款通常会在 3 至 5 个工作日到账。"
            return SimpleNamespace(
                response_id="response-1",
                seq=1,
                status=SimpleNamespace(value="selected"),
            )

    class BundleResolver:
        @staticmethod
        def resolve(_user_id):
            return SimpleNamespace(
                primary=SimpleNamespace(version="bundle-v1"),
                pinned_refs=None,
            )

    async def active_case(*_args, **_kwargs):
        events.append("active_case")
        return ActiveCaseContextView(
            ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
            ActiveCaseSelection((), ()),
        )

    async def knowledge(_message, **kwargs):
        events.append("knowledge")
        assert kwargs["required_by_plan"] is True
        assert kwargs["intent"] is None
        return SimpleNamespace(
            text="grounded evidence",
            used=True,
            citations=("chunk-1",),
            generation_status="grounded_draft",
            answer="退款通常会在 3 至 5 个工作日到账。",
            claims=(),
            conflicts=(),
            abstained=False,
            reason="",
            evidence_pack=None,
        )

    async def verify(*_args, **_kwargs):
        events.append("verification")
        return VerificationResult(
            VerificationStatus.PASS,
            True,
            False,
            "grounded",
            VerificationReasonCode.PASSED,
        )

    async def no_badcase(**_kwargs):
        return None

    async def no_intent_record(**_kwargs):
        raise AssertionError("command projection is not an Intent prediction")

    planner = CommandPrimaryChatPlanner(
        CommandPrimaryPlanner(
            PendingSlotResolver(lambda _signal, _message: None),
            KnowledgeSemantic(events),
            RoutePolicy(),
            TurnPlanCompiler(),
        ),
        knowledge_flow_registry,
        knowledge_primary=True,
        flow_state_store=FlowState(),
    )
    services = ChatServices(
        orchestrator=Orchestrator(),
        memory=Memory(),
        answer_verifier=object(),
        ticket_service=object(),
        response_delivery=Delivery(),
        context_assembler=ContextAssembler(),
        bundle_registry=object(),
        bundle_resolver=BundleResolver(),
        command_primary_chat_planner=planner,
    )
    operations = ChatOperations(
        active_ticket_context=active_case,
        build_knowledge_context=knowledge,
        capture_badcases=no_badcase,
        handoff_priority=lambda *_args: None,
        policy_terminal_verification=lambda _result: None,
        publish_candidate=lambda candidate, _verification: candidate,
        public_agent_outcomes=lambda outcomes: outcomes,
        record_intent_prediction=no_intent_record,
        select_publication_candidate=lambda _agent, evidence, **_kwargs: (
            evidence.answer,
            True,
        ),
        trace_id=lambda: "trace-1",
        verify_for_publication=verify,
    )

    outcome = asyncio.run(ChatApplication(services, operations).handle(ChatCommand(
        message="退款通常多久到账？",
        tenant_id="tenant-1",
        user_id="user-1",
        conv_id="conversation-1",
        request_id="request-1",
    )))

    assert isinstance(outcome, Completed)
    assert outcome.response["response"] == "退款通常会在 3 至 5 个工作日到账。"
    assert outcome.response["routing_policy_trace"]["decision_source"] == (
        "command_primary"
    )
    assert outcome.response["intent_prediction_id"] == ""
    assert events[:4] == ["state", "active_case", "flow_state", "understanding"]
    assert events[4:] == ["knowledge", "verification", "delivery", "memory_write"]
    stages = {stage.stage: stage for stage in outcome.stages}
    assert stages["intent"].status.value == "skipped"
    assert stages["route_path_plan"].detail["source"] == "command_primary"
