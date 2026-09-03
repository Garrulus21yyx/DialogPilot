"""Shared positive fixture for the command-primary sticky read-only chain."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

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
)
from application.command_primary_chat import CommandPrimaryChatPlanner
from application.command_primary_planner import CommandPrimaryPlanner
from application.default_flow_registry import command_primary_flow_registry
from application.flow_state import FlowStateAggregate
from application.route_decision import RouteMode
from application.route_policy_v2 import RoutePolicy
from application.turn_plan import TurnPlanCompiler
from application.turn_state import ActiveFlowRef, FlowBinding, FlowDefinitionRef
from application.turn_understanding import (
    CommandKind,
    CommandProposal,
    PendingSlotResolver,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
)
from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.customer_operations import CustomerOperationsService, OrderStatus


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
STICKY_COMMAND = ChatCommand(
    message="还是没到账",
    tenant_id="tenant-1",
    user_id="user-1",
    conv_id="conversation-1",
    request_id="request-1",
)


class InMemoryFlowState:
    def __init__(self) -> None:
        self.current = None

    def load(self, principal):
        if self.current is None:
            self.current = FlowStateAggregate.empty(
                principal,
                deletion_epoch=0,
            ).next(
                active_flows=(ActiveFlowRef(
                    FlowDefinitionRef("refund_status", "v1"),
                    "refund-status-1",
                    3,
                    principal.fingerprint,
                    (FlowBinding.create("order_id", "order-1"),),
                ),),
                pending_slot=None,
            )
        return self.current

    def compare_and_set(self, current, next_state):
        if current != self.current:
            return False
        self.current = next_state
        return True


class StickySemantic:
    def __init__(self) -> None:
        self.calls = 0

    async def understand(
        self,
        _message,
        message_fingerprint,
        state,
        _registry,
        **_context,
    ):
        self.calls += 1
        return UnderstandingResult(
            UnderstandingStatus.RESOLVED,
            (CommandProposal(
                CommandKind.CONTINUE_FLOW,
                UnderstandingSource.LLM,
                message_fingerprint,
                "command-router-test-v1",
                (f"message:{message_fingerprint}",),
                source_flow=state.active_flows[0],
            ),),
        )


@dataclass
class StickyReadOnlyHarness:
    application: ChatApplication
    flow_state: InMemoryFlowState
    semantic: StickySemantic
    tools: MCPToolManager
    events: list[str]

    def application_factory(self, _overrides: Any) -> ChatApplication:
        return self.application

    def flow_state_probe(self, _command: ChatCommand, _outcome: Any) -> dict[str, Any]:
        current = self.flow_state.current
        flow = current.active_flows[0]
        return {
            "aggregate_version": current.aggregate.version,
            "flow_id": flow.definition.flow_id,
            "flow_state_version": flow.state_version,
        }

    def tool_side_effects_probe(
        self,
        _command: ChatCommand,
        _outcome: Any,
    ) -> dict[str, Any]:
        return {
            "calls": tuple({
                "tool_name": record.tool_name,
                "status": record.status.value,
                "read_only": record.read_only,
                "effect_status": record.effect_status.value,
            } for record in self.tools.audit_records()),
        }


def build_sticky_read_only_harness(tmp_path: Any) -> StickyReadOnlyHarness:
    events: list[str] = []
    flow_state = InMemoryFlowState()
    semantic = StickySemantic()
    tools = _tool_manager(tmp_path)

    class Orchestrator:
        async def recognize_intent(self, *_args, **_kwargs):
            raise AssertionError("sticky primary path must not call legacy Intent")

        async def run(self, *_args, **_kwargs):
            raise AssertionError("compiled read-only work must not rerun Agent planning")

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

    async def active_case(*_args, **_kwargs):
        events.append("active_case")
        return ActiveCaseContextView(
            ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
            ActiveCaseSelection((), ()),
        )

    async def knowledge(*_args, **_kwargs):
        raise AssertionError("refund status is a business-tool read, not Knowledge RAG")

    async def verify(*_args, **kwargs):
        events.append("verification")
        assert kwargs["coverage"]["complete"] is True
        return VerificationResult(
            VerificationStatus.PASS,
            True,
            False,
            "authoritative read",
            VerificationReasonCode.PASSED,
        )

    class Delivery:
        @staticmethod
        def select_response(**kwargs):
            events.append("delivery")
            assert '"status":"requested"' in kwargs["response_text"]
            return SimpleNamespace(
                response_id="response-1",
                seq=1,
                status=SimpleNamespace(value="selected"),
            )

    planner = CommandPrimaryChatPlanner(
        CommandPrimaryPlanner(
            PendingSlotResolver(lambda _signal, _message: None),
            semantic,
            RoutePolicy(),
            TurnPlanCompiler(),
        ),
        command_primary_flow_registry,
        primary_route_modes=(RouteMode.AGENT_TASK,),
        flow_state_store=flow_state,
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
        command_primary_chat_planner=planner,
    )
    operations = ChatOperations(
        active_ticket_context=active_case,
        build_knowledge_context=knowledge,
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
        trace_id=lambda: "trace-1",
        verify_for_publication=verify,
    )
    return StickyReadOnlyHarness(
        application=ChatApplication(services, operations),
        flow_state=flow_state,
        semantic=semantic,
        tools=tools,
        events=events,
    )


def _tool_manager(tmp_path: Any) -> MCPToolManager:
    owner = CustomerOperationsService(
        str(tmp_path / "customer-operations.db"),
        clock=lambda: NOW,
    )
    order = owner.upsert_order(
        order_id="order-1",
        user_id="user-1",
        item_name="keyboard",
        amount_minor=39900,
        currency="CNY",
        status=OrderStatus.DELIVERED,
        refundable_until="2026-09-15T00:00:00+00:00",
    )
    owner.create_refund_request(
        idempotency_key="fixture-refund",
        user_id="user-1",
        order_id="order-1",
        expected_order_version=order.version,
        reason="fixture",
    )
    manager = MCPToolManager(api_key="test", model="test")
    for tool in customer_operation_tools(owner):
        manager.register(tool)
    return manager
