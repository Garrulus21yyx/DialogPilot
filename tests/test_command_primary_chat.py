from __future__ import annotations

import asyncio
import json
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
from application.route_decision import RouteMode
from core.model_policy import ModelProfile
from infrastructure.command_primary_runtime import build_command_primary_chat_planner
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)


def test_structured_knowledge_primary_runs_without_legacy_intent():
    events = []

    class Orchestrator:
        intent_calls = 0

        async def recognize_intent(self, *_args, **_kwargs):
            self.intent_calls += 1
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

    class Messages:
        def __init__(self):
            self.requests = []

        async def create(self, **request):
            events.append("understanding")
            self.requests.append(request)
            return SimpleNamespace(
                id="message-1",
                content=[
                    SimpleNamespace(
                        type="text",
                        text=json.dumps(
                            {
                                "status": "RESOLVED",
                                "commands": [
                                    {
                                        "kind": "ANSWER_KNOWLEDGE",
                                        "source_flow_instance_id": None,
                                        "target_flow_id": None,
                                        "target_flow_version": None,
                                    }
                                ],
                            }
                        ),
                    )
                ],
                usage=SimpleNamespace(input_tokens=20, output_tokens=10),
            )

    orchestrator = Orchestrator()
    messages = Messages()
    planner = build_command_primary_chat_planner(
        orchestrator,
        {"COMMAND_PRIMARY_MODE": "structured_knowledge_primary"},
        command_completion_client=SimpleNamespace(messages=messages),
        command_model_profile=ModelProfile("command-router-test"),
    )
    assert planner is not None
    services = ChatServices(
        orchestrator=orchestrator,
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

    outcome = asyncio.run(
        ChatApplication(services, operations).handle(
            ChatCommand(
                message="退款通常多久到账？",
                tenant_id="tenant-1",
                user_id="user-1",
                conv_id="conversation-1",
                request_id="request-1",
            )
        )
    )

    assert isinstance(outcome, Completed)
    assert outcome.response["response"] == "退款通常会在 3 至 5 个工作日到账。"
    assert outcome.response["routing_policy_trace"]["decision_source"] == (
        "command_primary"
    )
    assert outcome.response["intent_prediction_id"] == ""
    assert orchestrator.intent_calls == 0
    assert len(messages.requests) == 1
    prompt_input = json.loads(messages.requests[0]["messages"][0]["content"])
    assert prompt_input["encoder_candidates"] == []
    assert events == [
        "state",
        "active_case",
        "understanding",
        "knowledge",
        "verification",
        "delivery",
        "memory_write",
    ]
    stages = {stage.stage: stage for stage in outcome.stages}
    assert stages["intent"].status.value == "skipped"
    assert stages["route_path_plan"].detail["source"] == "command_primary"


def test_structured_knowledge_primary_accepts_existing_clarify_terminal():
    class Orchestrator:
        async def recognize_intent(self, *_args, **_kwargs):
            raise AssertionError("structured mode must not call legacy Intent")

    class Messages:
        async def create(self, **_request):
            return SimpleNamespace(
                id="message-clarify",
                content=[SimpleNamespace(
                    type="text",
                    text='{"status":"CLARIFY","commands":[]}',
                )],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    planner = build_command_primary_chat_planner(
        Orchestrator(),
        {"COMMAND_PRIMARY_MODE": "structured_knowledge_primary"},
        command_completion_client=SimpleNamespace(messages=Messages()),
        command_model_profile=ModelProfile("command-router-test"),
    )
    assert planner is not None
    active_case = ActiveCaseContextView(
        ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
        ActiveCaseSelection((), ()),
    )

    result = asyncio.run(planner.prepare(
        message="帮我看看这个是不是我需要的铭牌型号？",
        identity=SimpleNamespace(
            tenant_id="tenant-1",
            user_id="user-1",
            conversation_id="conversation-1",
            request_id="request-clarify",
        ),
        recent_messages=(),
        active_case_view=active_case,
        history=(),
        bundle=SimpleNamespace(version="bundle-v1"),
    ))

    assert result.plan is not None
    assert result.plan.route.mode is RouteMode.CLARIFY
    assert result.use_primary is True
