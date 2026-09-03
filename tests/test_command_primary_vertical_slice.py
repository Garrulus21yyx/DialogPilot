from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk
from application.command_primary_planner import (
    CommandPrimaryPlanner,
    PlanningStatus,
)
from application.route_decision import RouteMode
from application.route_policy_v2 import (
    ActionDefinition,
    ApprovalPolicy,
    FlowActionRegistry,
    FlowDefinition,
    RoutePolicy,
    RoutePolicyError,
    WorkKind,
)
from application.turn_plan import FlowMutationKind, TurnPlanCompiler
from application.turn_state import (
    ActiveFlowRef,
    FlowBinding,
    FlowAggregateVersion,
    FlowDefinitionRef,
    PendingSlotRef,
    PrincipalScope,
    StateAvailability,
    StateSourceStatus,
    TurnStateSnapshot,
)
from application.turn_understanding import (
    CommandArgument,
    CommandKind,
    CommandProposal,
    PendingSlotResolver,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
)


REFUND = FlowDefinitionRef("refund_status", "v1")


def state(*, pending: bool) -> TurnStateSnapshot:
    principal = PrincipalScope("tenant-1", "user-1", "conversation-1")
    flow = ActiveFlowRef(REFUND, "refund-flow-1", 7, principal.fingerprint)
    slot = (
        PendingSlotRef(
            "signal-order-id",
            4,
            flow.instance_id,
            "order_id",
            principal.fingerprint,
        )
        if pending else None
    )
    return TurnStateSnapshot(
        "request-1",
        principal,
        FlowAggregateVersion("flow-state:conversation-1", 9),
        (flow,),
        slot,
        ("turn-previous",),
        ("case-refund-1",),
        (),
        (StateSourceStatus(
            "flow_state", StateAvailability.CURRENT, "flow-store-v1",
        ),),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )


def registry() -> FlowActionRegistry:
    return FlowActionRegistry(
        tenant_id="tenant-1",
        generation="registry-v1",
        flows=(FlowDefinition(
            REFUND,
            (CommandKind.FILL_SLOT, CommandKind.CONTINUE_FLOW),
        ),),
        actions=(
            ActionDefinition(
                "refund.fill-order-id",
                "v1",
                CommandKind.FILL_SLOT,
                REFUND,
                WorkKind.AGENT,
                AgentType.BILLING,
                TaskEffect.READ_ONLY,
                TaskRisk.LOW,
                ("refund.status",),
                ("refund_status_lookup",),
                ApprovalPolicy.USER_COMMAND_SUFFICIENT,
                "query the bound refund after filling the order id",
            ),
            ActionDefinition(
                "refund.continue",
                "v1",
                CommandKind.CONTINUE_FLOW,
                REFUND,
                WorkKind.AGENT,
                AgentType.BILLING,
                TaskEffect.READ_ONLY,
                TaskRisk.LOW,
                ("refund.status",),
                ("refund_status_lookup",),
                ApprovalPolicy.USER_COMMAND_SUFFICIENT,
                "continue the active refund status flow",
            ),
            ActionDefinition(
                "knowledge.answer",
                "v1",
                CommandKind.ANSWER_KNOWLEDGE,
                None,
                WorkKind.KNOWLEDGE,
                AgentType.GENERAL,
                TaskEffect.READ_ONLY,
                TaskRisk.LOW,
                ("knowledge.policy",),
                (),
                ApprovalPolicy.USER_COMMAND_SUFFICIENT,
                "answer from grounded product knowledge",
            ),
        ),
    )


class SemanticStub:
    def __init__(self, result_factory=None) -> None:
        self.calls = 0
        self.seen_state = None
        self._result_factory = result_factory

    async def understand(
        self, message, message_fingerprint, snapshot, registry, **_context,
    ):
        self.calls += 1
        self.seen_state = snapshot
        return self._result_factory(message_fingerprint, snapshot)


def planner(semantic: SemanticStub) -> CommandPrimaryPlanner:
    deterministic = PendingSlotResolver(
        lambda _signal, message: message
        if re.fullmatch(r"DP\d{4}", message) else None,
    )
    return CommandPrimaryPlanner(
        deterministic,
        semantic,
        RoutePolicy(),
        TurnPlanCompiler(),
    )


def test_pending_slot_runs_state_first_without_encoder_or_llm() -> None:
    semantic = SemanticStub(lambda *_: pytest.fail("semantic router was called"))
    result = asyncio.run(
        planner(semantic).plan("DP1234", state(pending=True), registry())
    )

    assert result.status is PlanningStatus.PLANNED
    assert result.semantic_router_used is False
    assert semantic.calls == 0
    assert result.plan.route.mode is RouteMode.AGENT_TASK
    assert result.plan.transitions is not None
    assert result.plan.transitions.expected_aggregate_version == 9
    mutation = result.plan.transitions.mutations[0]
    assert mutation.kind is FlowMutationKind.FILL_SLOT
    assert mutation.expected_source_version == 7
    assert mutation.slot_consumption.expected_version == 4
    assert result.plan.work.items[0].allowed_tools == ("refund_status_lookup",)
    assert not hasattr(result.plan.route, "intent")


def test_semantic_router_receives_state_and_compiles_knowledge_work() -> None:
    def answer(message_fingerprint, snapshot):
        return UnderstandingResult(
            UnderstandingStatus.RESOLVED,
            (CommandProposal(
                CommandKind.ANSWER_KNOWLEDGE,
                UnderstandingSource.ENCODER,
                message_fingerprint,
                "encoder-router-v1",
                (f"message:{message_fingerprint}",),
            ),),
        )

    semantic = SemanticStub(answer)
    current = state(pending=False)
    result = asyncio.run(
        planner(semantic).plan("退款通常多久到账？", current, registry())
    )

    assert result.status is PlanningStatus.PLANNED
    assert result.semantic_router_used is True
    assert semantic.calls == 1
    assert semantic.seen_state is current
    assert result.plan.route.mode is RouteMode.KNOWLEDGE_QA
    assert result.plan.transitions is None
    assert result.plan.work.graph.tasks[0].requirement_ids == (
        "knowledge.policy",
    )


def test_sticky_continuation_uses_the_exact_active_flow() -> None:
    def continue_flow(message_fingerprint, snapshot):
        return UnderstandingResult(
            UnderstandingStatus.RESOLVED,
            (CommandProposal(
                CommandKind.CONTINUE_FLOW,
                UnderstandingSource.LLM,
                message_fingerprint,
                "command-router-v1",
                (f"message:{message_fingerprint}",),
                source_flow=snapshot.active_flows[0],
            ),),
        )

    current = state(pending=False)
    result = asyncio.run(
        planner(SemanticStub(continue_flow)).plan(
            "还是没到账",
            current,
            registry(),
        )
    )

    assert result.plan.transitions is not None
    mutation = result.plan.transitions.mutations[0]
    assert mutation.kind is FlowMutationKind.ADVANCE
    assert mutation.source_instance_id == current.active_flows[0].instance_id
    assert mutation.expected_source_version == 7


def test_continuation_inherits_required_bindings_and_turn_delta_wins() -> None:
    current = state(pending=False)
    active = replace(
        current.active_flows[0],
        bindings=(
            FlowBinding.create("order_id", "DP-OLD"),
            FlowBinding.create("locale", "zh-CN"),
        ),
    )
    current = replace(current, active_flows=(active,))
    base_registry = registry()
    actions = tuple(
        replace(
            action,
            required_arguments=("order_id", "locale"),
        )
        if action.command_kind is CommandKind.CONTINUE_FLOW else action
        for action in base_registry.actions
    )
    current_registry = replace(base_registry, actions=actions)
    proposal = CommandProposal(
        CommandKind.CONTINUE_FLOW,
        UnderstandingSource.LLM,
        "a" * 64,
        "command-router-v1",
        ("message:a",),
        source_flow=active,
        arguments=(CommandArgument.create("order_id", "DP-NEW"),),
    )

    accepted = RoutePolicy().accept(
        UnderstandingResult(UnderstandingStatus.RESOLVED, (proposal,)),
        current,
        current_registry,
    )
    plan = TurnPlanCompiler().compile(accepted, current, current_registry)
    arguments = {
        item.name: item.value for item in plan.work.items[0].arguments
    }

    assert arguments == {"locale": "zh-CN", "order_id": "DP-NEW"}


def test_route_policy_rejects_a_command_without_registered_action() -> None:
    current = state(pending=False)
    message_fingerprint = "a" * 64
    understanding = UnderstandingResult(
        UnderstandingStatus.RESOLVED,
        (CommandProposal(
            CommandKind.REQUEST_HANDOFF,
            UnderstandingSource.LLM,
            message_fingerprint,
            "command-router-v1",
            ("message:a",),
        ),),
    )
    with pytest.raises(RoutePolicyError, match="no action registered"):
        RoutePolicy().accept(understanding, current, registry())
