"""Whole-turn approval/goal algebra, independent of any retail example."""
import asyncio
from dataclasses import replace

import pytest

from application.agent_result import RequestedField
from application.conversation_agent import ConversationAgent, planning_output_schema
from application.conversation_state import PendingApprovalState, PendingInteractionState, WorkstreamState, WorkstreamStatus
from application.deterministic_resolution import TurnObservations
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from application.turn_planning import CommandKind, CommandProposal, TurnProposal, ProposalDisposition, TurnPlanningError
from application.work_item import ArgumentValue
from tests.test_conversation_agent import Provider
from tests.test_target_persistence_and_manager import _identity
from tests.test_work_recovery import _setup


@pytest.mark.parametrize("decision", [None, "approve", "decline"])
@pytest.mark.parametrize("goal", [None, "continue", "revise", "cancel", "fields", "independent"])
@pytest.mark.parametrize("same_thread", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_whole_turn_preserves_unaddressed_waits_and_exact_authorization(decision, goal, same_thread, typed):
    async def run():
        async def unexpected_execution(context):
            raise AssertionError("preparing a plan cannot execute a tool")
        manager, _, store = _setup(unexpected_execution)
        registry = manager._registry
        action = replace(registry.action("order.cancel:v1"), flow_ref=None)
        manager._registry = replace(registry, actions=tuple(action if a.ref == action.ref else a for a in registry.actions))
        manager._understanding.initial = TurnProposal(ProposalDisposition.RESOLVED, (
            CommandProposal("origin", CommandKind.DELEGATE_TASK, "order_logistics", "Requested action", allow_action_proposals=True),
            CommandProposal("origin-child", CommandKind.DELEGATE_TASK, "order_logistics", "Use action result", dependencies=("origin",)),
            CommandProposal("fields", CommandKind.DELEGATE_TASK, "product_technical", "Choose an option"),
            CommandProposal("child", CommandKind.DELEGATE_TASK, "product_technical", "Use chosen option", dependencies=("fields",)),
            CommandProposal("peer", CommandKind.DELEGATE_TASK, "order_logistics", "Independent query"),
        ), "TEST")
        initial = await manager.prepare(_identity("initial"), TurnObservations("Initial objectives"))
        manager._commit_states(initial.state_before, initial.state_transitions)
        origin, origin_child, field, child, peer = initial.plan.work.items
        state = initial.state.wait_for_approval(PendingApprovalState(
            "approval", 1, "action-stream", "prepared-action", action.ref, "operation",
            "order:example", "4", "2099-01-01T00:00:00+00:00",
            arguments=(ArgumentValue.create("order_id", "example"), ArgumentValue.create("expected_order_version", 4)),
            checkpoint_thread_id="approval-thread", suspended_work_items=(origin, origin_child, peer),
            origin_work_item_id=origin.work_item_id, control=origin.control),
            new_workstream=WorkstreamState("action-stream", origin.owner_agent, action.ref,
                "PREPARED", WorkstreamStatus.WAITING_APPROVAL, 1))
        assert store.compare_and_set(initial.state, state)
        approval_state = state
        state = state.wait_for_interaction(PendingInteractionState("input", 1,
            (RequestedField("reply", field.work_item_id, "string"),), (), (field, child),
            "approval-thread" if same_thread else "input-thread"))
        assert store.compare_and_set(approval_state, state)
        value = {"status": "resolved"}
        if decision:
            value["approval_decision"] = {"approval_id": "approval", "decision": decision}
        if goal:
            item = {"kind": "continue_active_work", "revises_control_id": origin.control.control_id}
            if goal == "fields":
                item["revises_control_id"] = field.control.control_id
            elif goal == "cancel":
                item["kind"] = "cancel_active_work"
            elif goal in {"revise", "independent"}:
                item = {"kind": "delegate_task", "target_agent": "order_logistics",
                    "objective": "Changed objective" if goal == "revise" else "New query",
                    "allow_action_proposals": goal == "revise"}
                if goal == "revise":
                    item["revises_control_id"] = origin.control.control_id
            value["goals"] = [item]
        if not decision and not goal:
            value = {"status": "insufficient_context", "missing_fields": ["customer_service_goal"]}
        from jsonschema import validate
        validate(value, planning_output_schema())
        provider = Provider(value)
        manager._understanding = CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider))
        observations = TurnObservations("Complete user reply", **({
            "approval_id": "approval", "approval_decision": decision == "approve"} if typed and decision else {}))
        if typed and decision and goal == "fields":
            observations = replace(observations, raw_text="", interaction_id="input", interaction_version=1,
                interaction_values=((field.work_item_id, "reply", "blue"),))
        if decision == "approve" and goal in {"revise", "cancel"}:
            with pytest.raises(TurnPlanningError, match="goal is changed"):
                await manager.prepare(_identity("reply"), observations)
            assert store.load(state.tenant_id, state.user_id, state.conversation_id) == state
            return
        if decision is None and goal == "continue":
            with pytest.raises(TurnPlanningError, match="requires an approval decision"):
                await manager.prepare(_identity("reply"), observations)
            assert store.load(state.tenant_id, state.user_id, state.conversation_id) == state
            return
        prepared = await manager.prepare(_identity("reply"), observations)
        assert len(provider.calls) == 1
        assert provider.calls[0]["pending_approval"]["arguments"]["expected_order_version"] == 4
        assert store.load(state.tenant_id, state.user_id, state.conversation_id) == state
        items = prepared.plan.work.items if prepared.plan.work else ()
        controls = [item.control.control_id for item in items]
        assert len(controls) == len(set(controls))
        writes = [item for item in items if item.operation_key]
        assert len(writes) == int(decision == "approve")
        if writes:
            assert writes[0].operation_key == "operation"
            assert writes[0].arguments == state.pending_approval.arguments
        assert (prepared.state.pending_interaction is None) == (goal == "fields")
        if goal != "fields":
            assert field.control.control_id not in controls and child.control.control_id not in controls
        assert (prepared.state.pending_approval is None) == bool(decision or goal in {"revise", "cancel", "continue"})
        cancelled = {m.control_id for m in prepared.plan.control_mutations}
        if decision == "decline" and goal not in {"revise", "continue", "cancel"}:
            assert origin.control.control_id in cancelled
            assert origin_child.control.control_id in cancelled
        if decision == "decline" and goal == "continue":
            root = next(item for item in items if item.control.control_id == origin.control.control_id)
            dependent = next(item for item in items if item.control.control_id == origin_child.control.control_id)
            assert root.work_item_id in dependent.dependencies
            assert not cancelled
        if goal == "revise":
            revised = next(item for item in items if item.control.control_id == origin.control.control_id)
            assert revised.objective == "Changed objective" and revised.continuation_of is None
        if decision and goal == "fields":
            assert len(prepared.source_thread_ids) == int(not same_thread)
            if typed:
                resumed = next(item for item in items if item.control.control_id == field.control.control_id)
                assert {arg.name: arg.value for arg in resumed.arguments}["reply"] == "blue"
    asyncio.run(run())
