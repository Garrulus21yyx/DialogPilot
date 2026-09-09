"""Accepted siblings survive partial revision, cancellation and reconstruction."""
from dataclasses import replace
from itertools import permutations

import pytest

from application.agent_result import AgentResult, AgentResultStatus, RequestedField
from application.conversation_state import ConversationState, PendingInteractionState, WorkControlStatus
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.work_item import WorkControlBinding, WorkPlan, ControlMode
from application.result_board import ResultBoard
from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
from tests.test_target_orchestration_runtime import _item, _fact


def waiting_items(count=3):
    items = tuple(replace(_item(str(i), "general", ControlMode.DELEGATED, f"fact.{i}"),
                          control=WorkControlBinding(f"goal-{i}", 1)) for i in range(count))
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    state = state.accept_work_items(items, invocation_key="initial")
    state = state.wait_for_interaction(PendingInteractionState("input", 1,
        tuple(RequestedField("reply", w.work_item_id, "string", "Which option?") for w in items),
        (), items, "checkpoint"))
    return state, items


@pytest.mark.parametrize("cancel", [False, True])
def test_all_revision_orders_preserve_unaffected_waits_and_reject_old_signals(cancel):
    for order in permutations(range(3)):
        state, items = waiting_items()
        remaining = set(range(3))
        for index in order:
            old = state.pending_interaction
            if cancel:
                state = state.close_work_control(items[index].control, status=WorkControlStatus.CANCELLED)
            else:
                revised = replace(items[index], work_item_id=f"new-{index}",
                    control=WorkControlBinding(f"goal-{index}", 2))
                state = state.accept_work_items((revised,), invocation_key=f"revision-{index}")
            remaining.remove(index)
            state = conversation_state_from_payload(conversation_state_to_payload(state))
            assert f"interaction:input:v{old.version}" in state.consumed_signal_ids
            if remaining:
                pending = state.pending_interaction
                assert pending.version == old.version + 1
                assert pending.checkpoint_thread_id == "checkpoint"
                assert {f.target_work_item_id for f in pending.requested_fields} == {str(i) for i in remaining}
                assert set(pending.suspended_work_items) == {items[i] for i in remaining}
                # Each remaining field is still genuinely resumable, not just a label.
                one = next(iter(remaining))
                resolution = DeterministicResolver().resolve(TurnObservations("", interaction_id="input",
                    interaction_version=pending.version, interaction_values=((str(one), "reply", "blue"),)), state)
                assert resolution.resumed_work_items[0].work_item_id == str(one)
            else:
                assert state.pending_interaction is None


def test_revision_retires_dependent_questions_but_not_independent_questions():
    state, items = waiting_items()
    dependent = replace(items[2], dependencies=(items[0].work_item_id,))
    state = replace(state, pending_interaction=replace(state.pending_interaction,
        suspended_work_items=(items[0], items[1], dependent)))
    changed = state.close_work_control(items[0].control, status=WorkControlStatus.CANCELLED)
    assert changed.pending_interaction.suspended_work_items == (items[1],)
    assert [field.target_work_item_id for field in changed.pending_interaction.requested_fields] == ["1"]
    # Dependency invalidation does not silently cancel the dependent user's goal.
    assert next(c for c in changed.work_controls if c.control_id == "goal-2").status is WorkControlStatus.ACTIVE


@pytest.mark.parametrize("omitted_status", [None, AgentResultStatus.PARTIAL,
    AgentResultStatus.BLOCKED, AgentResultStatus.RETRYABLE_FAILURE, AgentResultStatus.TERMINAL_FAILURE,
    AgentResultStatus.SUCCEEDED])
def test_replanning_a_subset_cannot_claim_omitted_work_completed(omitted_status):
    _, items = waiting_items(2)
    old = None if omitted_status is None else AgentResult(items[1].work_item_id, items[1].owner_agent,
        omitted_status, "OBSERVED", "test", retryable=omitted_status is AgentResultStatus.RETRYABLE_FAILURE,
        facts=(_fact(items[1], "done"),) if omitted_status is AgentResultStatus.SUCCEEDED else ())
    revised = replace(items[0], work_item_id="revised", control=WorkControlBinding("goal-0", 2))
    plan = WorkPlan((revised,), revised.work_item_id)
    prior = ((items[0], None), (items[1], old))
    retained = plan.unreplaced_outcomes(prior)
    assert retained == ((items[1], old),)
    succeeded = AgentResult(revised.work_item_id, revised.owner_agent, AgentResultStatus.SUCCEEDED,
        "DONE", "test", facts=(_fact(revised, "done"),))
    board = ResultBoard().evaluate(plan, (succeeded,), retained_outcomes=retained)
    assert len(board.outcome_items) == 2
    assert board.task_completed is (omitted_status is AgentResultStatus.SUCCEEDED)
    assert board.coverage_for(items[1], old)["task_completed"] is (omitted_status is AgentResultStatus.SUCCEEDED)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_manager_changes_one_goal_then_resumes_the_other_without_repeating_it(cancel, backend, request):
    import asyncio
    from application.agent_result import MissingInputSpec
    from application.turn_planning import CommandKind, CommandProposal, TurnProposal, ProposalDisposition
    from tests.test_task_result_lifecycle import _setup
    from core.identity import IdentityFactory
    from uuid import uuid4
    conversation = "conservation-" + uuid4().hex
    def identity(request_id):
        return IdentityFactory().create_invocation(tenant_id="tenant-target", user_id="user-target",
            conversation_id=conversation, request_id=request_id)
    async def run(saver=None, store=None):
        calls = []
        async def worker(context):
            item = context.work_item
            calls.append(item.owner_agent)
            if not item.continuation_of and item.control.revision == 1:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Which option?"),))
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, "DONE", "test")
        manager, _, _ = _setup(worker, checkpointer=saver, store=store)
        initial = await manager.handle(identity("initial"), TurnObservations("Handle both tasks"))
        pending = initial.state_after.pending_interaction
        first, second = pending.suspended_work_items
        original_understanding = manager._understanding
        async def understanding(observations, state, deterministic, registry, context):
            if observations.raw_text == "Change only the first task":
                return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal("change",
                    CommandKind.CANCEL_WORK if cancel else CommandKind.DELEGATE_TASK,
                    first.owner_agent, "Changed first objective", revises_control_id=first.control.control_id),), "CHANGE")
            return await original_understanding(observations, state, deterministic, registry, context)
        manager._understanding = understanding
        changed = await manager.handle(identity("change"), TurnObservations("Change only the first task"))
        remaining = changed.state_after.pending_interaction
        assert remaining is not None
        assert remaining.suspended_work_items == (second,)
        assert remaining.version == pending.version + 1
        calls_before = len(calls)
        finished = await manager.handle(identity("answer"), TurnObservations("", interaction_id=remaining.interaction_id,
            interaction_version=remaining.version, interaction_values=((second.work_item_id, "reply", "blue"),)))
        assert finished.state_after.pending_interaction is None
        assert len(calls) == calls_before + 1 and calls[-1] == second.owner_agent
        assert {item.control.control_id for item, _ in finished.board.outcome_items} == {
            first.control.control_id, second.control.control_id}
        original_result = next(result for item, result in finished.board.outcome_items
                               if item.control.control_id == first.control.control_id)
        assert original_result.status is (AgentResultStatus.CANCELLED if cancel else AgentResultStatus.SUCCEEDED)
    async def scenario():
        if backend == "memory":
            await run()
        else:
            from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
            from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
            from infrastructure.postgres_target_runtime import PostgresConversationStateStore
            url = request.getfixturevalue("postgres_database_url")
            PostgresMigrationRunner(url).upgrade()
            pool = PostgresPool(PostgresPoolConfig(url, min_size=1, max_size=2))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                    await run(saver, PostgresConversationStateStore(pool))
            finally:
                pool.close()
    asyncio.run(scenario())
