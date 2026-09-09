"""A held decision is handled once; read observations do not consume it again."""
import asyncio

import pytest

from application.conversation_actions import action_proposal, planning_actions
from application.conversation_agent import ConversationAgent
from application.conversation_state import PendingApprovalState, WorkstreamState, WorkstreamStatus
from application.deterministic_resolution import TurnObservations
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from tests.test_conversation_actions import calls
from tests.test_target_persistence_and_manager import _identity
from tests.test_task_result_lifecycle import _setup
from tests.test_turn_runtime import _Executor


@pytest.mark.parametrize("typed_decision", [True, False])
def test_held_approval_read_observation_replies_without_reconsuming_current_input(typed_decision):
    async def run():
        executor = _Executor()
        manager, _, store = _setup(executor)
        initial = await manager.prepare(_identity("initial"), TurnObservations("Help with my order"))
        manager._commit_states(initial.state_before, initial.state_transitions)
        origin = next(item for item in initial.plan.work.items if item.owner_agent == "order_logistics")
        pending = PendingApprovalState(
            "approval", 1, "action-stream", "prepared-action", "order.cancel:v1", "operation",
            "order:DP1234", "4", "2099-01-01T00:00:00+00:00",
            checkpoint_thread_id="approval-thread", suspended_work_items=(origin,),
            origin_work_item_id=origin.work_item_id, control=origin.control,
        )
        waiting = initial.state.wait_for_approval(pending, new_workstream=WorkstreamState(
            "action-stream", origin.owner_agent, pending.action_ref,
            "PREPARED", WorkstreamStatus.WAITING_APPROVAL, 1))
        assert store.compare_and_set(initial.state, waiting)
        captured = []

        class Provider:
            async def plan(self, payload):
                captured.append(payload)
                actions = planning_actions(payload)
                if len(captured) == 1:
                    return action_proposal(actions, calls(
                        ("review_action", {"decision": "hold"}),
                        ("order_lookup", {"order_id": "DP1234"})), "")
                assert "review_action" not in {action.name for action in actions}
                return action_proposal(actions, calls(("respond", {"response":
                    "The order has shipped. The cancellation remains unapproved."})), "Private draft")

        catalog = ({"owner_agent": "order_logistics", "tool_id": "order_lookup",
                    "description": "Read the order's current state.",
                    "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                                     "required": ["order_id"], "additionalProperties": False},
                    "requirement_ids": ("order.current_state",)},)
        manager._understanding = CascadedTargetUnderstanding(StateBoundTargetUnderstanding(),
            ConversationAgent(Provider(), tool_catalog=lambda *_args: catalog))
        original = TurnObservations("First check whether it has shipped; I will decide afterwards.",
                                    approval_id="approval", approval_decision=typed_decision)
        prepared = await manager.prepare(_identity("conditional"), original)
        assert prepared.state.pending_approval == pending
        assert len(prepared.plan.work.items) == 1
        assert prepared.plan.work.items[0].observe_result
        read = await manager.execute(prepared)
        await manager.commit_progress(read)
        read = await manager.resolve_followup(prepared, read)
        await manager.commit(read)
        following = await manager.prepare_observation(prepared, read)
        assert following.plan.route.reason_code == "CONVERSATION_RESPONSE"
        assert following.plan.response_text == "The order has shipped. The cancellation remains unapproved."
        assert following.observations == original  # Keep the audit input, not a rewritten user turn.
        assert following.state.pending_approval == pending
        assert following.state.accepted_approvals == ()
        assert following.state.consumed_signal_ids == waiting.consumed_signal_ids
        assert executor.calls == 1
        assert read.board.facts
        assert all(not result.action_receipts for result in read.board.all_results)
        assert captured[0]["current_user_decision"]["decision"] == ("approve" if typed_decision else "decline")
        assert captured[1]["current_user_decision"] is None
        assert captured[1]["message"] == original.raw_text
        assert captured[1]["pending_approval"] == captured[0]["pending_approval"]
        assert captured[1]["conversation_context"]["observed_execution"] is not None
        answered = await manager.execute(following)
        await manager.commit_progress(answered)
        answered = await manager.resolve_followup(following, answered)
        await manager.commit(answered)
        assert answered.state_after.pending_approval == pending
        assert answered.state_after.accepted_approvals == ()
        assert answered.board.facts == read.board.facts
        assert all(not result.action_receipts for result in answered.board.all_results)
        assert executor.calls == 1

    asyncio.run(run())
