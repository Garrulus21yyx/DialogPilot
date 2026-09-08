"""Manual review remains a paused, unconfirmed task across public replay."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from application.agent_result import AgentResult, AgentResultStatus
from application.chat_contracts import ChatCommand, Completed
from application.conversation_state import ConversationState, WorkstreamState, WorkstreamStatus
from application.deterministic_resolution import ResolutionKind
from application.orchestration_runtime import OrchestrationRuntime
from application.result_board import ResultBoard
from application.target_conversation_manager import TargetConversationManager
from application.work_item import WorkPlan
from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
from tests.test_write_workflow import _item
from tests.test_target_chat_cutover import _application
from tests.test_postgres_response_delivery import compat_components, _metadata


def manual_result(item):
    return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.BLOCKED,
        "WRITE_MANUAL_REVIEW_REQUIRED", "governed-write-runtime-v1",
        execution_feedback=({"stage": "write_recovery", "status": "MANUAL_REVIEW",
            "operation_key": item.operation_key, "ticket_id": "ticket:" + item.operation_key,
            "business_outcome": "UNCONFIRMED", "reason": "RECOVERY_BUDGET_EXHAUSTED",
            "recovery_attempts": 3},))


@pytest.mark.parametrize("count", [1, 3])
def test_publication_keeps_each_manual_review_and_independent_success(monkeypatch, count):
    execute = OrchestrationRuntime.execute

    async def with_manual_history(self, *args, **kwargs):
        board = await execute(self, *args, **kwargs)
        retained = []
        for index in range(count):
            item = replace(_item(f"operation-{index}"), work_item_id=f"write-{index}")
            retained.append((item, manual_result(item)))
        return ResultBoard().evaluate(WorkPlan(board.work_items, board.work_items[0].work_item_id),
            board.results, retained_outcomes=tuple(retained))

    monkeypatch.setattr(OrchestrationRuntime, "execute", with_manual_history)
    app, tools = _application()
    command = ChatCommand("查一下订单 DP1234 物流", "user-a", "tenant-a", "conversation-a", "manual-history")
    result = asyncio.run(app.handle(command))
    assert isinstance(result, Completed)  # Response published; business task incomplete.
    assert result.response["recovery_status"] == "MANUAL_REVIEW"
    assert not result.response["task_completed"]
    assert "DP1234" in result.response["response"]
    assert {r["operation_key"] for r in result.response["manual_review_actions"]} == {
        f"operation-{index}" for index in range(count)}
    assert all(r["business_outcome"] == "UNCONFIRMED" for r in result.response["manual_review_actions"])
    calls = len(tools.calls)
    assert asyncio.run(app.handle(command)) == result and len(tools.calls) == calls


def test_manager_persists_manual_pause_without_completing_the_workstream():
    item = _item()
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    state = state.start_workstream(WorkstreamState("stream", item.owner_agent, item.action_ref,
        "EXECUTE", WorkstreamStatus.ACTIVE, 1))
    work = WorkPlan((item,), item.work_item_id)
    board = ResultBoard().evaluate(work, (manual_result(item),))
    plan = SimpleNamespace(work=work, control_mutations=())
    resolution = SimpleNamespace(kind=ResolutionKind.APPROVAL_DECISION, approved=True,
        operation_key=item.operation_key, signal_id=item.approval_binding, workstream_id="stream")
    transitions = []
    next_state = TargetConversationManager._apply_successful_workflows(None, state, plan, board,
        None, resolution, checkpoint_thread_id="thread", transitions=transitions)
    assert next_state.workstreams[0].status is WorkstreamStatus.PAUSED
    assert next_state.workstreams[0].phase == "MANUAL_REVIEW"
    restored = conversation_state_from_payload(conversation_state_to_payload(next_state))
    assert restored == next_state
    assert restored.mark_workstream_manual_review("stream",
        expected_version=restored.workstreams[0].state_version) is restored
    assert not board.task_completed and len(transitions) == 1


def test_postgres_response_replay_keeps_unconfirmed_manual_review(compat_components):
    _, identity, service = compat_components
    public = {"recovery_status": "MANUAL_REVIEW", "task_completed": False,
        "manual_review_actions": list(manual_result(_item()).execution_feedback)}
    service.select_response(user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id), response_text="业务结果尚未确认，等待人工核实。",
        identity_metadata={**_metadata(identity), "public_response": public})
    replay = service.completed_for_invocation(identity.invocation_key, user_id=str(identity.user_id))
    assert isinstance(replay, Completed)
    assert all(replay.response[key] == value for key, value in public.items())
