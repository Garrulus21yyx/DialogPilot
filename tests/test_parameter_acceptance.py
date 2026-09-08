"""Selections use their snapshot; continuations retain accepted provenance."""
from dataclasses import replace

import pytest

from application.agent_result import RequestedField
from application.conversation_state import PendingInteractionState, WorkControlStatus
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import BindingStatus
from application.target_understanding import StateBoundTargetUnderstanding
from application.turn_planning import CommandKind, CommandProposal, RoutePolicy, TurnPlanCompiler, TurnPlanningError, TurnProposal, ProposalDisposition
from application.work_item import ArgumentValue
from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
from tests.test_entity_binding import _state, _context
from application.conversation_state import WorkstreamState, WorkstreamStatus
from application.entity_binding import EntityBindingResolver
from application.default_capability_registry import build_default_capability_registry
from core.identity import IdentityFactory


def proposal(command):
    return TurnProposal(ProposalDisposition.RESOLVED, (command,), "TEST")


def accepted_work(kind=CommandKind.DIRECT_TOOL):
    registry = build_default_capability_registry("tenant-a")
    stream = WorkstreamState("source", "order_logistics", "order_status", "READY",
        WorkstreamStatus.ACTIVE, 1, slots=(ArgumentValue.create("order_id", "DP1111"),))
    state = _state(version=1, workstreams=(stream,))
    binding = EntityBindingResolver().resolve(TurnObservations("status"), state, _context()).resolve("order_id", state).selected
    command = CommandProposal("read", kind, "order_logistics", "Read requested order",
        arguments=(ArgumentValue.create("order_id", "DP1111"),),
        requirement_ids=("order.current_state",), argument_bindings=(binding,),
        tool_id="order_lookup" if kind is CommandKind.DIRECT_TOOL else None)
    invocation = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="first")
    plan = TurnPlanCompiler().compile(RoutePolicy().accept(proposal(command), state, registry), state, registry, invocation)
    item, = plan.work.items
    state = state.accept_work_items((item,), invocation_key=str(invocation.invocation_key))
    state = state.wait_for_interaction(PendingInteractionState("input", 1,
        (RequestedField("reply", item.work_item_id, "string", "Which option?"),), (), (item,), "thread"))
    return state, registry, item, command


@pytest.mark.parametrize("kind", [CommandKind.DIRECT_TOOL, CommandKind.DELEGATE_TASK])
@pytest.mark.parametrize("phase,status", [("READY", WorkstreamStatus.ACTIVE),
    ("COMPLETE", WorkstreamStatus.COMPLETED), ("SUPERSEDED", WorkstreamStatus.CANCELLED)])
@pytest.mark.parametrize("roundtrip", [False, True])
def test_accepted_value_survives_carrier_change_but_new_selection_uses_current_value(kind, phase, status, roundtrip):
    state, registry, item, command = accepted_work(kind)
    changed = replace(state, version=state.version + 1, workstreams=(replace(state.workstreams[0],
        phase=phase, status=status, state_version=2, slots=(ArgumentValue.create("order_id", "DP2222"),)),))
    if roundtrip:
        changed = conversation_state_from_payload(conversation_state_to_payload(changed))
    continuation = StateBoundTargetUnderstanding._resume_command(1, item)
    accepted = RoutePolicy().accept(proposal(continuation), changed, registry)
    assert accepted.commands[0].proposal.arguments == item.arguments
    assert accepted.commands[0].proposal.argument_bindings == item.argument_bindings
    assert item.argument_bindings[0].valid_for(changed) is BindingStatus.STALE
    with pytest.raises(TurnPlanningError, match="stale or unauthorized"):
        RoutePolicy().accept(proposal(command), changed, registry)
    if status is WorkstreamStatus.ACTIVE:
        selected = EntityBindingResolver().resolve(TurnObservations("status"), changed, _context()).resolve("order_id", changed).selected
        assert selected.value == "DP2222" and selected.source_version == 2


def test_new_selection_is_validated_before_local_transition_not_after_it():
    state, registry, _, command = accepted_work()
    changed = replace(state, version=state.version + 1,
        workstreams=(replace(state.workstreams[0], state_version=2),))
    result = RoutePolicy().accept(proposal(command), changed, registry, planning_state=state)
    assert result.state_fingerprint == changed.fingerprint
    with pytest.raises(TurnPlanningError, match="stale or unauthorized"):
        RoutePolicy().accept(proposal(command), changed, registry, planning_state=changed)
    from application.turn_planning import PlanningInvariantError
    with pytest.raises(PlanningInvariantError, match="another conversation"):
        RoutePolicy().accept(proposal(command), changed, registry,
            planning_state=replace(state, user_id="different-user"))


@pytest.mark.parametrize("mutation", ["parameter", "provenance", "envelope", "cancel", "scope"])
def test_continuation_cannot_reauthorize_changed_parameters_or_envelopes(mutation):
    state, registry, item, _ = accepted_work()
    command = StateBoundTargetUnderstanding._resume_command(1, item)
    if mutation == "parameter":
        binding = replace(item.argument_bindings[0], value_json='"DP9999"')
        command = replace(command, arguments=(ArgumentValue.create("order_id", "DP9999"),), argument_bindings=(binding,))
    elif mutation == "provenance":
        command = replace(command, argument_bindings=(replace(item.argument_bindings[0], source_ref="invented"),))
    elif mutation == "envelope":
        command = replace(command, resumed_work_item=replace(item, timeout_seconds=item.timeout_seconds + 1))
    elif mutation == "cancel":
        state = state.close_work_control(item.control, status=WorkControlStatus.CANCELLED)
    else:
        state = replace(state, user_id="different-user")
    with pytest.raises(TurnPlanningError):
        RoutePolicy().accept(proposal(command), state, registry)


@pytest.mark.parametrize("wait_consumed", [False, True])
def test_typed_input_extends_only_bound_fields_without_refreshing_old_provenance(wait_consumed):
    state, registry, item, _ = accepted_work()
    changed = replace(state, workstreams=(replace(state.workstreams[0], state_version=2),))
    # Test both before and after owner consumption of the wait. Compound
    # approval with fields is covered separately in the whole-turn matrix.
    obs = TurnObservations("", interaction_id="input", interaction_version=1,
        interaction_values=((item.work_item_id, "reply", "blue"),))
    result = DeterministicResolver().resolve(obs, changed)
    updated, = result.resumed_work_items
    assert updated.argument_bindings[0] == item.argument_bindings[0]
    after = replace(changed, pending_interaction=None) if wait_consumed else changed
    accepted = RoutePolicy().accept(proposal(StateBoundTargetUnderstanding._resume_command(1, updated)),
        after, registry, continuation_items=result.resumed_work_items)
    assert dict((arg.name, arg.value) for arg in accepted.commands[0].proposal.arguments) == {
        "order_id": "DP1111", "reply": "blue"}
    with pytest.raises(TurnPlanningError):
        RoutePolicy().accept(proposal(StateBoundTargetUnderstanding._resume_command(1, item)),
            after, registry, continuation_items=result.resumed_work_items)


@pytest.mark.parametrize("kind", [CommandKind.DIRECT_TOOL, CommandKind.DELEGATE_TASK])
def test_same_local_work_id_cannot_exchange_accepted_envelopes_between_controls(kind):
    state, registry, item, _ = accepted_work(kind)
    other = replace(item, control=replace(item.control, control_id="another-control"))
    state = state.accept_work_items((other,), invocation_key="another-invocation")
    # Both envelopes are trusted, with equal local IDs, owner and revision.
    # Neither can authorize a continuation of the other's control.
    for target, wrong in ((item, other), (other, item)):
        command = StateBoundTargetUnderstanding._resume_command(1, target)
        RoutePolicy().accept(proposal(command), state, registry, continuation_items=(other,))
        with pytest.raises(TurnPlanningError, match="parameters differ"):
            RoutePolicy().accept(proposal(replace(command, resumed_work_item=wrong)), state, registry,
                continuation_items=(other,))


def test_manager_accepts_new_selection_from_snapshot_before_declining_proposal():
    import asyncio
    from application.conversation_state import PendingApprovalState
    from application.conversation_agent import ConversationAgent
    from application.target_understanding import CascadedTargetUnderstanding
    from tests.test_conversation_agent import Provider
    from tests.test_work_recovery import _setup
    from tests.test_target_persistence_and_manager import _identity

    async def run():
        async def no_execution(context):
            pytest.fail("acceptance must not execute tools")
        manager, _, store = _setup(no_execution)
        manager._understanding.initial = proposal(CommandProposal("original", CommandKind.DELEGATE_TASK,
            "order_logistics", "Requested change", allow_action_proposals=True))
        initial = await manager.prepare(_identity("initial"), TurnObservations("Initial"))
        manager._commit_states(initial.state_before, initial.state_transitions)
        original, = initial.plan.work.items
        order = ArgumentValue.create("order_id", "DP1234")
        pending = PendingApprovalState("approval", 1, "proposal-source", "prepared-action",
            "order.cancel:v1", "operation", "order:DP1234", "4", "2099-01-01T00:00:00+00:00",
            arguments=(order, ArgumentValue.create("expected_order_version", 4)),
            checkpoint_thread_id="approval-thread", suspended_work_items=(original,),
            origin_work_item_id=original.work_item_id, control=original.control)
        state = initial.state.wait_for_approval(pending, new_workstream=WorkstreamState(
            "proposal-source", "order_logistics", "order.cancel:v1", "PREPARED",
            WorkstreamStatus.WAITING_APPROVAL, 1, slots=(order,)))
        assert store.compare_and_set(initial.state, state)
        provider = Provider({"status": "resolved", "approval_decision": {
            "approval_id": "approval", "decision": "decline"}, "goals": [{
                "kind": "delegate_task", "target_agent": "order_logistics", "objective": "Revised request",
                "revises_control_id": original.control.control_id, "allow_action_proposals": True,
                "order_id": "DP1234", "order_id_source_ref": "workstream:proposal-source:slot:order_id"}]})
        manager._understanding = CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider))
        prepared = await manager.prepare(_identity("revise"), TurnObservations("No, change the request"))
        revised, = prepared.plan.work.items
        assert revised.arguments == (order,)
        assert revised.argument_bindings[0].source_version == 1
        assert next(s for s in prepared.state.workstreams if s.workstream_id == "proposal-source").state_version > 1
        assert len(provider.calls) == 1
        assert store.load(state.tenant_id, state.user_id, state.conversation_id) == state
    asyncio.run(run())
