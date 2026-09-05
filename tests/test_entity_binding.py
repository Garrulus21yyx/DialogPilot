import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from application.conversation_state import (
    ConversationState,
    WorkstreamState,
    WorkstreamStatus,
)
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import (
    BindingSource,
    BindingStatus,
    EntityBinding,
    EntityBindingResolver,
    EntityBindingSet,
)
from application.target_conversation_manager import (
    TargetContextMessage,
    TargetContextProjectionStatus,
    TargetTurnContext,
)
from application.target_understanding import BoundedTargetUnderstanding
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    RoutePolicy,
    TurnPlanningError,
    TurnPlanCompiler,
    TurnProposal,
    ProposalDisposition,
)
from application.work_item import ArgumentValue
from core.identity import IdentityFactory
from infrastructure.postgres_target_runtime import (
    conversation_state_from_payload,
    conversation_state_to_payload,
)


def _state(*, version=0, workstreams=()):
    return ConversationState(
        "tenant-a", "user-a", "conversation-a", version,
        workstreams=workstreams,
    )


def _context(*messages):
    return TargetTurnContext(
        recent_messages=messages,
        projection_status=TargetContextProjectionStatus.READY,
        projection_reason_codes=(),
    )


def test_current_input_has_priority_over_unrelated_history():
    state = _state()
    context = _context(TargetContextMessage(
        "user", "订单 DP1111", "event:1", 1,
    ))
    bindings = EntityBindingResolver().resolve(
        TurnObservations("查询订单 DP2222"), state, context,
    )

    selected = bindings.resolve("order_id", state)

    assert selected.status is BindingStatus.UNIQUE
    assert selected.selected.value == "DP2222"
    assert selected.selected.source is BindingSource.CURRENT_MESSAGE


def test_equal_priority_conflicting_candidates_are_ambiguous():
    state = _state()
    context = _context(
        TargetContextMessage("user", "DP1111", "event:1", 1),
        TargetContextMessage("user", "DP2222", "event:2", 2),
    )
    bindings = EntityBindingResolver().resolve(
        TurnObservations("查询订单状态"), state, context,
    )

    resolution = bindings.resolve("order_id", state)

    assert resolution.status is BindingStatus.AMBIGUOUS
    assert {item.value for item in resolution.candidates} == {"DP1111", "DP2222"}


def test_expired_and_cross_scope_bindings_have_typed_outcomes():
    state = _state()
    expired = EntityBinding.create(
        "order_id", "DP1111", source=BindingSource.RECENT_MESSAGE,
        source_ref="event:1", tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", priority=200,
        valid_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    foreign = replace(expired, valid_until=None, tenant_id="tenant-b")

    assert EntityBindingSet((expired,)).resolve(
        "order_id", state,
    ).status is BindingStatus.STALE
    assert EntityBindingSet((foreign,)).resolve(
        "order_id", state,
    ).status is BindingStatus.UNAUTHORIZED


def test_route_policy_revalidates_workstream_binding_version():
    stream = WorkstreamState(
        "ws-1", "order_logistics", "order_status", "READY",
        WorkstreamStatus.ACTIVE, 1,
        slots=(ArgumentValue.create("order_id", "DP1111"),),
    )
    state = _state(version=1, workstreams=(stream,))
    binding = EntityBindingResolver().resolve(
        TurnObservations("查询订单状态"), state, _context(),
    ).resolve("order_id", state).selected
    command = CommandProposal(
        "order", CommandKind.DIRECT_TOOL, "order_logistics", "Query order",
        (ArgumentValue.create("order_id", "DP1111"),),
        ("order.current_state",), tool_id="order_lookup",
        argument_bindings=(binding,),
    )
    changed = replace(
        state,
        workstreams=(replace(stream, state_version=2),),
        version=2,
    )

    with pytest.raises(TurnPlanningError, match="stale or unauthorized"):
        RoutePolicy().accept(
            TurnProposal(
                ProposalDisposition.RESOLVED, (command,), "TEST_PLAN",
            ),
            changed,
            build_default_capability_registry("tenant-a"),
        )


def test_unique_historical_binding_removes_the_early_missing_id_branch():
    state = _state()
    observations = TurnObservations("查询订单状态")
    context = _context(TargetContextMessage(
        "user", "订单 DP1111", "event:1", 1,
    ))
    context = replace(
        context,
        entity_bindings=EntityBindingResolver().resolve(
            observations, state, context,
        ),
    )
    deterministic = DeterministicResolver().resolve(observations, state)
    registry = build_default_capability_registry("tenant-a")

    proposal = asyncio.run(BoundedTargetUnderstanding()(
        observations, state, deterministic, registry, context,
    ))
    validated = RoutePolicy().accept(proposal, state, registry)
    plan = TurnPlanCompiler().compile(
        validated,
        state,
        registry,
        IdentityFactory().create_invocation(
            tenant_id="tenant-a", user_id="user-a",
            conversation_id="conversation-a", request_id="request-a",
        ),
    )

    assert proposal.disposition is ProposalDisposition.RESOLVED
    assert plan.work.items[0].arguments[0].value == "DP1111"
    assert plan.work.items[0].argument_bindings[0].source_ref.startswith("event:1")


def test_binding_provenance_survives_conversation_state_round_trip():
    state = _state()
    observations = TurnObservations("查询订单 DP1111")
    binding = EntityBindingResolver().resolve(
        observations, state, _context(),
    ).resolve("order_id", state).selected
    workstream = WorkstreamState(
        "ws-1", "order_logistics", "order_status", "READY",
        WorkstreamStatus.ACTIVE, 1,
        slots=(ArgumentValue.create("order_id", "DP1111"),),
        slot_bindings=(binding,),
    )
    stored = replace(state, version=1, workstreams=(workstream,))

    restored = conversation_state_from_payload(conversation_state_to_payload(stored))

    assert restored == stored
