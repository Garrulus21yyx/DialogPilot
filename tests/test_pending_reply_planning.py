"""Bound reply identity must not override the user's current objective."""
import asyncio
import pytest

from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
from application.conversation_agent import ConversationAgent
from application.deterministic_resolution import TurnObservations
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal
from tests.test_conversation_agent import Provider
from tests.test_target_persistence_and_manager import _identity
from tests.test_task_result_lifecycle import _setup


@pytest.mark.parametrize("decision", ["continue", "revise", "cancel", "unrelated", "failure", "clarify", "invalid"])
@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize("explicit_binding", [True, False])
def test_bound_reply_plan_owns_goal_and_wait_lifecycle(decision, depth, explicit_binding):
    async def run():
        seen = []
        async def worker(context):
            item = context.work_item
            seen.append(item)
            if item.objective == "Original scope" and not item.continuation_of:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Which option?"),))
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, "DONE", "test")

        manager, runtime, store = _setup(worker)
        commands = (CommandProposal("root", CommandKind.DELEGATE_TASK, "product_technical", "Original scope"),
                    *(CommandProposal(f"d{i}", CommandKind.DELEGATE_TASK, "product_technical", f"Dependent {i}",
                        dependencies=("root" if i == 0 else f"d{i-1}",)) for i in range(depth)),
                    CommandProposal("peer", CommandKind.DELEGATE_TASK, "order_logistics", "Independent"))
        manager._understanding.initial = TurnProposal(ProposalDisposition.RESOLVED, commands, "TEST")
        initial = await manager.handle(_identity("initial"), TurnObservations("Original request"))
        pending = initial.state_after.pending_interaction
        original = pending.suspended_work_items[0]
        ref = original.control.control_id
        value = {"status": "resolved", "goals": [{
            "kind": "continue_active_work", "revises_control_id": ref}]}
        if decision in {"revise", "unrelated"}:
            value["goals"] = [{"kind": "delegate_task", "target_agent": "product_technical",
                "objective": "Corrected scope" if decision == "revise" else "Another task",
                "allow_action_proposals": False,
                **({"revises_control_id": ref} if decision == "revise" else {})}]
        elif decision == "cancel":
            value["goals"] = [{"kind": "cancel_active_work", "revises_control_id": ref}]
        elif decision == "clarify":
            value = {"status": "insufficient_context", "missing_fields": ["customer_service_goal"]}
        elif decision == "invalid":
            value["goals"][0]["revises_control_id"] = "not-an-active-goal"
        provider = Provider(value, ConnectionError("offline") if decision == "failure" else None)
        class Encoder:
            async def __call__(self, *args):
                from types import SimpleNamespace
                assert not explicit_binding, "bound free text must reach the contextual planner"
                return SimpleNamespace(accepted=False)
        manager._understanding = CascadedTargetUnderstanding(StateBoundTargetUnderstanding(),
            ConversationAgent(provider), encoder=Encoder())
        observation = TurnObservations("Current user meaning", **({
            "interaction_id": pending.interaction_id, "interaction_version": pending.version}
            if explicit_binding else {}))
        if decision in {"failure", "invalid"}:
            from application.turn_planning import PlanningUnavailable
            with pytest.raises(PlanningUnavailable):
                await manager.prepare(_identity("reply"), observation)
            identity = _identity("reply")
            assert store.load(identity.tenant_id, identity.user_id, identity.conversation_id) == initial.state_after
            return
        prepared = await manager.prepare(_identity("reply"), observation)
        assert len(provider.calls) == 1
        assert provider.calls[0]["pending_input"]["objectives"][0]["objective"] == "Original scope"
        if decision in {"unrelated", "failure", "clarify", "invalid"}:
            assert prepared.state.pending_interaction == pending
            assert prepared.resume_thread_id == (pending.checkpoint_thread_id if decision == "unrelated" else None)
            if decision == "unrelated":
                result = await manager.execute(prepared)
                result = await manager.commit_progress(result, prepared=prepared)
                result = await manager.resolve_followup(prepared, result)
                await manager.commit(result)
                assert result.state_after.pending_interaction == pending
                assert sum(item.objective == "Original scope" for item in seen) == 1
            assert prepared.state.consumed_signal_ids == initial.state_after.consumed_signal_ids
            return
        assert prepared.state.pending_interaction is None
        assert len(prepared.state_transitions) == 1
        assert prepared.state.version == initial.state_after.version + 1
        assert prepared.resume_thread_id == pending.checkpoint_thread_id
        if decision == "cancel":
            assert prepared.plan.work is None
            assert len(prepared.plan.control_mutations) == depth + 1
            assert {item.work_item_id for item in manager._closed_work_items(
                prepared.state_before, prepared.deterministic, prepared.plan)} == {
                item.work_item_id for item in pending.suspended_work_items}
        else:
            root = prepared.plan.work.items[0]
            assert root.objective == ("Original scope" if decision == "continue" else "Corrected scope")
            assert root.continuation_of == (original.work_item_id if decision == "continue" else None)
            assert root.control.revision == original.control.revision + 1
            assert len(prepared.plan.work.items) == depth + 1
            assert all(item.dependencies for item in prepared.plan.work.items[1:])
        result = await manager.execute(prepared)
        result = await manager.commit_progress(result, prepared=prepared)
        result = await manager.resolve_followup(prepared, result)
        await manager.commit(result)
        assert sum(item.objective == "Independent" for item in seen) == 1
        assert result.state_after.pending_interaction is None
        assert decision == "cancel" or result.board.task_completed
        with pytest.raises(ValueError):
            await manager.prepare(_identity("replay"), TurnObservations("Old reply",
                interaction_id=pending.interaction_id, interaction_version=pending.version))
    asyncio.run(run())


@pytest.mark.parametrize("transition", ["revise", "cancel", "close"])
@pytest.mark.parametrize("peer_count", [1, 3, 7])
def test_input_retirement_preserves_independent_resume_tokens(transition, peer_count):
    from dataclasses import replace
    from application.agent_result import RequestedField
    from application.conversation_state import ConversationState, PendingInteractionState, ResumeBinding, WorkControlStatus
    from tests.test_conversation_state_resolution import _workstream
    from tests.test_work_control import _item
    item = _item("goal", 1, work_item_id="asking")
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    state = state.accept_work_items((item,), invocation_key="initial")
    for index in range(peer_count):
        state = state.start_workstream(_workstream(f"independent-{index}"))
    tokens = tuple(ResumeBinding(f"token-{index}", f"independent-{index}", 1) for index in range(peer_count))
    state = replace(state, resume_bindings=tokens)
    state = state.wait_for_interaction(PendingInteractionState("input", 1,
        (RequestedField("reply", item.work_item_id, "string"),), (), (item,), "thread"))
    if transition == "revise":
        updated = state.accept_work_items((_item("goal", 2, work_item_id="new"),), invocation_key="new")
    elif transition == "cancel":
        updated = state.accept_work_items((), invocation_key="new", cancelled_controls=(item.control,))
    else:
        updated = state.close_work_control(item.control, status=WorkControlStatus.CANCELLED)
    assert updated.pending_interaction is None
    assert updated.resume_bindings == tokens
    assert updated.version == state.version + 1


@pytest.mark.parametrize("kind", ["typed_input", "approval"])
def test_signal_consumption_invalidates_only_its_own_resume_binding(kind):
    from dataclasses import replace
    from application.agent_result import RequestedField
    from application.conversation_state import PendingInteractionState, PendingApprovalState, ResumeBinding
    from tests.test_conversation_state_resolution import _state, _workstream
    state = _state(_workstream("target"), _workstream("peer"))
    if kind == "typed_input":
        state = state.wait_for_interaction(PendingInteractionState("input", 1,
            (RequestedField("order_id", "target", "string"),), ()))
    else:
        state = state.wait_for_approval(PendingApprovalState("approval", 1, "target", "work",
            "registered:v1", "operation", "entity", "1", "2099-01-01T00:00:00+00:00"))
    versions = {item.workstream_id: item.state_version for item in state.workstreams}
    target = ResumeBinding("target-token", "target", versions["target"])
    peer = ResumeBinding("peer-token", "peer", versions["peer"])
    state = replace(state, resume_bindings=(target, peer))
    if kind == "typed_input":
        updated = state.consume_interaction(interaction_id="input", interaction_version=1,
                                             values=(("target", "order_id", "A123"),))
    else:
        updated = state.consume_approval(approval_id="approval", approval_version=1, approved=True)
    assert updated.resume_bindings == (peer,)
