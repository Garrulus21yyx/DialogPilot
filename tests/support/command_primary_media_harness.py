"""Positive command-primary L1 media fixture."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

from agents.media_requirement import LocalMediaRequirementAgent
from application.active_case import (
    ActiveCaseContextView,
    ActiveCaseProjection,
    ActiveCaseSelection,
    ActiveCaseState,
)
from application.chat_application import ChatApplication, ChatOperations, ChatServices
from application.chat_contracts import ChatCommand
from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import (
    MEDIA_TEXT_READ,
    command_primary_flow_registry,
)
from application.flow_state import FlowStateAggregate
from application.media_requirement import MediaRequirementValidator
from application.perception import TieredPerceptionService
from application.route_decision import RouteMode
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
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.response_delivery import DeliveryStatus
from tests.support.media_l1_chat_harness import BASE_COMMAND, _asset_store, _ocr


class FlowState:
    def __init__(self) -> None:
        self.current: FlowStateAggregate | None = None

    def load(self, principal: Any) -> FlowStateAggregate:
        if self.current is None:
            self.current = FlowStateAggregate.empty(principal, deletion_epoch=0)
        return self.current

    def compare_and_set(
        self,
        current: FlowStateAggregate,
        next_state: FlowStateAggregate,
    ) -> bool:
        if current != self.current:
            return False
        self.current = next_state
        return True


class MediaSemantic:
    async def understand(
        self,
        _message: str,
        message_fingerprint: str,
        _state: Any,
        _registry: Any,
        **_context: Any,
    ) -> UnderstandingResult:
        return UnderstandingResult(
            UnderstandingStatus.RESOLVED,
            (CommandProposal(
                CommandKind.START_FLOW,
                UnderstandingSource.LLM,
                message_fingerprint,
                "command-router-test-v1",
                (f"message:{message_fingerprint}",),
                target_flow=MEDIA_TEXT_READ,
            ),),
        )


class ExplicitMediaAgent(LocalMediaRequirementAgent):
    def __init__(self) -> None:
        self.calls = 0
        self.task: dict[str, Any] = {}

    async def decide_media_requirement(self, *, task: Any, asset_ids: Any) -> Any:
        self.calls += 1
        self.task = dict(task)
        return await super().decide_media_requirement(
            task=task,
            asset_ids=asset_ids,
        )

    def policies_for_task(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("command-primary must use the Action policy")


@dataclass
class CommandPrimaryMediaHarness:
    application: ChatApplication
    command: ChatCommand
    media_agent: ExplicitMediaAgent
    ocr: Any
    flow_state: FlowState
    persisted_messages: list[str]


def build_command_primary_media_harness() -> CommandPrimaryMediaHarness:
    store = _asset_store(BASE_COMMAND)
    command = replace(BASE_COMMAND, asset_ids=(store.asset.asset_id,))
    media_agent = ExplicitMediaAgent()
    ocr = _ocr()
    flow_state = FlowState()
    persisted_messages: list[str] = []

    class Orchestrator:
        async def recognize_intent(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("command-primary must not call legacy Intent")

        async def run(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("compiled media work must not rerun Agent routing")

    class Memory:
        async def get_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                recent_messages=[],
                retrieval_hits=[],
                to_sections=lambda: [],
            )

        async def add_messages(
            self,
            _user_id: str,
            _conv_id: str,
            messages: list[Any],
            **_kwargs: Any,
        ) -> None:
            persisted_messages.extend(item[1] for item in messages)

    async def active_case(*_args: Any, **_kwargs: Any) -> Any:
        return ActiveCaseContextView(
            ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
            ActiveCaseSelection((), ()),
        )

    async def verify(*_args: Any, **kwargs: Any) -> VerificationResult:
        assert kwargs["coverage"] == {
            "complete": True,
            "media_requirement_id": "media.visible_text",
            "media_artifact_refs": ["parse-media-l1-e2e"],
        }
        return VerificationResult(
            VerificationStatus.PASS,
            True,
            False,
            "L1 evidence consumed",
            VerificationReasonCode.PASSED,
        )

    class Delivery:
        @staticmethod
        def select_response(**kwargs: Any) -> Any:
            assert kwargs["response_text"] == "识别到的文字：E401"
            return SimpleNamespace(
                response_id="response-command-media-l1",
                seq=1,
                status=DeliveryStatus.SELECTED,
            )

    planner = CommandPrimaryChatPlanner(
        CommandPrimaryPlanner(
            PendingSlotResolver(lambda _signal, _message: None),
            MediaSemantic(),
            RoutePolicy(),
            TurnPlanCompiler(),
        ),
        command_primary_flow_registry,
        primary_route_modes=(RouteMode.AGENT_TASK,),
        flow_state_store=flow_state,
    )
    tools = SimpleNamespace(
        audit_records=lambda **_kwargs: [],
        execute_for_agent=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("media read must not call a business tool")
        ),
    )
    services = ChatServices(
        orchestrator=Orchestrator(),
        memory=Memory(),
        answer_verifier=object(),
        ticket_service=object(),
        response_delivery=Delivery(),
        context_assembler=SimpleNamespace(
            assemble=lambda **_kwargs: SimpleNamespace(system_context=""),
        ),
        bundle_registry=object(),
        bundle_resolver=SimpleNamespace(
            resolve=lambda _user_id: SimpleNamespace(
                primary=SimpleNamespace(version="bundle-v1"),
                pinned_refs=None,
            ),
        ),
        tool_manager=tools,
        media_requirement_agent=media_agent,
        media_requirement_validator=MediaRequirementValidator(),
        media_asset_store=store,
        perception_service=TieredPerceptionService(store, ocr=ocr, vlm=None),
        command_primary_chat_planner=planner,
    )
    operations = ChatOperations(
        active_ticket_context=active_case,
        build_knowledge_context=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("media read must not call Knowledge RAG")
        ),
        capture_badcases=lambda **_kwargs: asyncio.sleep(0),
        handoff_priority=lambda *_args: None,
        policy_terminal_verification=lambda _result: None,
        publish_candidate=lambda candidate, _verification: candidate,
        public_agent_outcomes=lambda outcomes: outcomes,
        record_intent_prediction=lambda **_kwargs: asyncio.sleep(0),
        select_publication_candidate=lambda agent, _evidence, **_kwargs: (
            agent,
            False,
        ),
        trace_id=lambda: "trace-command-media-l1",
        verify_for_publication=verify,
    )
    return CommandPrimaryMediaHarness(
        ChatApplication(services, operations),
        command,
        media_agent,
        ocr,
        flow_state,
        persisted_messages,
    )
