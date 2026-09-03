"""Fixed semantic stub for one non-scoring CLARIFY transport smoke."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from application.chat_application import ChatApplication, ChatOperations, ChatServices
from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import command_primary_flow_registry
from application.route_decision import RouteMode
from application.route_policy_v2 import RoutePolicy
from application.turn_plan import TurnPlanCompiler
from application.turn_understanding import (
    ClarificationDecision,
    ClarificationReason,
    PendingSlotResolver,
    UnderstandingResult,
    UnderstandingStatus,
)
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.response_delivery import DeliveryStatus


class ClarifySemanticStub:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def understand(
        self,
        message: str,
        _message_fingerprint: str,
        _state: Any,
        _registry: Any,
        **_context: Any,
    ) -> UnderstandingResult:
        self.messages.append(message)
        return UnderstandingResult(
            UnderstandingStatus.CLARIFY,
            reason_code="SEMANTIC_INPUT_INSUFFICIENT",
            clarification=ClarificationDecision(
                ClarificationReason.MISSING_REFERENT,
                ("product_or_media_reference",),
            ),
        )


@dataclass
class CommandPrimaryClarifyHarness:
    application: ChatApplication
    semantic: ClarifySemanticStub
    published: list[str]
    persisted: list[str]


def build_command_primary_clarify_harness() -> CommandPrimaryClarifyHarness:
    semantic = ClarifySemanticStub()
    published: list[str] = []
    persisted: list[str] = []

    class Orchestrator:
        async def recognize_intent(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("CLARIFY primary must not call legacy Intent")

        async def run(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("CLARIFY terminal must not run an Agent")

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
            persisted.extend(item[1] for item in messages)

    class Delivery:
        @staticmethod
        def select_response(**kwargs: Any) -> Any:
            published.append(kwargs["response_text"])
            return SimpleNamespace(
                response_id="response-clarify-smoke",
                seq=1,
                status=DeliveryStatus.SELECTED,
            )

    planner = CommandPrimaryChatPlanner(
        CommandPrimaryPlanner(
            PendingSlotResolver(lambda _signal, _message: None),
            semantic,
            RoutePolicy(),
            TurnPlanCompiler(),
        ),
        command_primary_flow_registry,
        primary_route_modes=(RouteMode.CLARIFY,),
    )
    verification = VerificationResult(
        VerificationStatus.PASS,
        True,
        False,
        "policy terminal",
        VerificationReasonCode.POLICY_TERMINAL,
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
        tool_manager=SimpleNamespace(audit_records=lambda **_kwargs: []),
        command_primary_chat_planner=planner,
    )
    operations = ChatOperations(
        active_ticket_context=lambda *_args, **_kwargs: asyncio.sleep(0),
        build_knowledge_context=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CLARIFY terminal must not call Knowledge RAG")
        ),
        capture_badcases=lambda **_kwargs: asyncio.sleep(0),
        handoff_priority=lambda *_args: None,
        policy_terminal_verification=lambda _result: verification,
        publish_candidate=lambda candidate, _verification: candidate,
        public_agent_outcomes=lambda outcomes: outcomes,
        record_intent_prediction=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("CLARIFY primary must not record legacy Intent")
        ),
        select_publication_candidate=lambda agent, _evidence, **_kwargs: (
            agent,
            False,
        ),
        trace_id=lambda: "trace-clarify-smoke",
        verify_for_publication=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("policy terminal must not call semantic verifier")
        ),
    )
    return CommandPrimaryClarifyHarness(
        ChatApplication(services, operations),
        semantic,
        published,
        persisted,
    )
