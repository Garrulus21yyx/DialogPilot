"""Outcome/goal algebra on the real SDK graph; semantic judgments are scripted."""
import asyncio
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from tests.test_approval_conversation import domain, call


def terminal(tool, text, ident="terminal"):
    return AIMessage(content="", tool_calls=[{"name": tool, "id": ident,
        "args": {"question" if tool == "request_user_input" else "reason": text}}])


@pytest.mark.parametrize("candidate", [
    AIMessage(content="Which order would you like to cancel?"),
    terminal("request_user_input", "Should I cancel the order you specified?"),
    terminal("report_blocked", "The request is already complete; nothing else is needed."),
])
def test_all_terminal_routes_reconsider_within_sdk_before_durable_handback(candidate):
    agent, context, model, calls = domain([candidate, call("prepare_order_cancel"),
                                         AIMessage(content="Cancellation is prepared, not executed.")])
    model.outcome_reviews = [
        {"accepted": False, "feedback": "The target is known; prepare its requested cancellation."},
        {"accepted": True, "feedback": ""}]
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL"
    assert result.pending_action is not None
    assert not result.missing_inputs and not result.action_receipts
    assert len(calls) == 1  # Preparation read, no business write or rejected input execution.
    assert model.calls == 3 and model.review_calls == 1
    assert [entry["accepted"] for entry in result.execution_feedback
            if entry["stage"] == "domain_outcome"] == [False]
    assert not any((message["data"].get("artifact") or {}).get("schema") == "agent-result-v1"
        for message in result.working_messages if message["type"] == "tool"
        and message["data"].get("name") in {"request_user_input", "report_blocked"})


@pytest.mark.parametrize("kind,text,status", [
    ("request_user_input", "Which of the two orders?", "NEEDS_USER_INPUT"),
    ("report_blocked", "No permitted capability can access this order.", "BLOCKED"),
])
def test_valid_handback_stays_bound_to_assigned_work(kind, text, status):
    agent, context, model, calls = domain([terminal(kind, text)])
    result = asyncio.run(agent(context))
    assert result.status.value == status
    assert result.work_item_id == context.work_item.work_item_id
    assert model.review_calls == 1 and model.calls == 1 and not calls
    if result.missing_inputs:
        assert result.missing_inputs[0].target_work_item_id == result.work_item_id


@pytest.mark.parametrize("candidate", [AIMessage(content="Done"),
    terminal("request_user_input", "Proceed?"), terminal("report_blocked", "Already done")])
def test_two_rejections_are_typed_non_success_without_pending_input(candidate):
    agent, context, model, calls = domain([candidate, candidate.model_copy(update={"id": None})])
    model.outcome_reviews = [{"accepted": False, "feedback": "Assigned objective remains unfulfilled."}] * 2
    result = asyncio.run(agent(context))
    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "DOMAIN_OUTCOME_REJECTED"
    assert not result.missing_inputs and not result.pending_action and not calls
    assert model.calls == 2 and model.review_calls == 2
    assert any(item["stage"] == "domain_outcome" for item in result.execution_feedback)


def test_unavailable_assessment_is_not_silent_success_or_ordinary_agent_failure():
    agent, context, model, _ = domain([AIMessage(content="Completed")])
    model.outcome_reviews = [{"accepted": True, "feedback": "contradiction"}]
    result = asyncio.run(agent(context))
    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "DOMAIN_OUTCOME_REVIEW_UNAVAILABLE"
    assert not result.missing_inputs


def test_resolved_receipt_does_not_need_another_write_or_blocker():
    from application.agent_result import AgentResult, AgentResultStatus, ReceiptRef
    agent, context, model, calls = domain([
        terminal("report_blocked", "Already cancelled; there is no further work."),
        AIMessage(content="Cancellation is completed.")])
    done = AgentResult("action", context.work_item.owner_agent, AgentResultStatus.SUCCEEDED,
        "COMMITTED", "test", action_receipts=(ReceiptRef("receipt", "v1", "operation",
                                                    "COMMITTED", "order.cancel_action"),))
    context = replace(context, dependency_results=(done,))
    model.outcome_reviews = [{"accepted": False, "feedback": "The receipt satisfies the requested action; report completion, not a blocker."},
                            {"accepted": True, "feedback": ""}]
    result = asyncio.run(agent(context))
    assert result.status.value == "SUCCEEDED"
    assert not calls and not result.pending_action


@pytest.mark.parametrize("independent_status", ["SUCCEEDED", "BLOCKED", "TERMINAL_FAILURE"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("reuse_local_id", [False, True])
def test_resume_does_not_erase_other_goal_outcomes(independent_status, reverse, reuse_local_id):
    from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
    from application.orchestration_runtime import OrchestrationRuntime
    from application.work_item import WorkPlan, WorkControlBinding
    from application.response_assembly import _response_context
    from langgraph.checkpoint.memory import InMemorySaver
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    from tests.test_target_framework_agent import _item

    async def run():
        first = replace(_item(), work_item_id="first", requirement_ids=(),
                        objective="Count available variants", control=WorkControlBinding("count", 1))
        second = replace(first, work_item_id="second", objective="Return the requested products",
                         control=WorkControlBinding("return", 1))
        calls = []
        async def worker(context):
            item = context.work_item
            calls.append(item.work_item_id)
            if item.work_item_id == "second":
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Which return method?"),))
            return AgentResult(item.work_item_id, item.owner_agent,
                AgentResultStatus(independent_status) if item.control.control_id == "count" else AgentResultStatus.SUCCEEDED,
                "TEST", "test", candidate_response="Recorded outcome")
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        def runtime():
            return OrchestrationRuntime(direct_executor=worker, domain_workers={first.owner_agent: worker}, checkpointer=saver)
        items = (second, first) if reverse else (first, second)
        await runtime().execute(WorkPlan(items, items[0].work_item_id), current_message="Count and return", thread_id="goals")
        resumed = replace(second, work_item_id="first" if reuse_local_id else "resumed",
                          continuation_of="second", control=WorkControlBinding("return", 2))
        board = await runtime().resume(WorkPlan((resumed,), resumed.work_item_id), current_message="Pickup", thread_id="goals")
        assert board.task_completed is (independent_status == "SUCCEEDED")
        assert len(board.all_results) == 2
        assert calls.count("first") == (2 if reuse_local_id else 1)
        outcomes = _response_context(board)["outcomes"]
        assert {outcome["objective"] for outcome in outcomes} == {first.objective, second.objective}
        assert next(outcome for outcome in outcomes if outcome["control"]["control_id"] == "count")["status"] == independent_status
        # Reopening the completed resume uses the same board contract, including
        # retained goals; it cannot collapse back to current-plan-only success.
        again = await runtime().resume(WorkPlan((resumed,), resumed.work_item_id), current_message="Pickup", thread_id="goals")
        assert again == board
    asyncio.run(run())


@pytest.mark.parametrize("status_code,retryable", [(429, True), (503, True), (401, False)])
def test_assessment_preserves_provider_retryability(status_code, retryable):
    from core.framework_models import ModelInvocationError
    from tests.test_target_framework_agent import ScriptedToolModel
    class ProviderError(RuntimeError):
        pass
    class UnavailableModel(ScriptedToolModel):
        def with_structured_output(self, schema, **kwargs):
            error = ProviderError("unavailable")
            error.status_code = status_code
            raise ModelInvocationError("assess_domain_outcome", error) from error
    agent, context, _, _ = domain([])
    agent._model = UnavailableModel(responses=[AIMessage(content="Complete")])
    result = asyncio.run(agent(context))
    assert result.reason_code == "DOMAIN_OUTCOME_REVIEW_UNAVAILABLE"
    assert result.retryable is retryable


@pytest.mark.parametrize("backend", ["memory", "postgres"])
@pytest.mark.parametrize("independent", [False, True])
def test_explicit_closure_removes_wait_but_preserves_unrelated_work(backend, independent, request):
    from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
    from application.orchestration_runtime import OrchestrationRuntime
    from application.work_item import WorkPlan, WorkControlBinding
    from langgraph.checkpoint.memory import InMemorySaver
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer, AsyncPostgresCheckpointOwner
    from tests.test_target_framework_agent import _item
    from uuid import uuid4

    async def run(saver):
        origin = replace(_item(), requirement_ids=(), work_item_id="origin", control=WorkControlBinding("origin", 1))
        other = replace(origin, work_item_id="other", control=WorkControlBinding("other", 1))
        seen = []
        async def worker(context):
            item = context.work_item
            seen.append(item.work_item_id)
            if item.work_item_id == "origin":
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Select a target"),))
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, "DONE", "test")
        def runtime():
            return OrchestrationRuntime(direct_executor=worker, domain_workers={origin.owner_agent: worker}, checkpointer=saver)
        thread = uuid4().hex
        items = (origin, other) if independent else (origin,)
        await runtime().execute(WorkPlan(items, "origin"), current_message="Do this", thread_id=thread)
        if independent:
            new = replace(other, work_item_id="resumed", continuation_of="other", control=WorkControlBinding("other", 2))
            board = await runtime().resume(WorkPlan((new,), "resumed"), current_message="No", thread_id=thread,
                                           closed_work_items=(origin,))
        else:
            await runtime().cancel_interrupt(thread_id=thread, closed_work_items=(origin,))
            board = (await runtime().graph.aget_state({"configurable": {"thread_id": thread}})).values["board"]
        original = next(result for result in board.all_results if result.work_item_id == "origin")
        assert original.status is AgentResultStatus.CANCELLED and not original.missing_inputs
        assert not board.task_completed and seen.count("origin") == 1
    if backend == "memory":
        asyncio.run(run(InMemorySaver(serde=target_checkpoint_serializer())))
    else:
        url = request.getfixturevalue("postgres_database_url")
        async def postgres():
            async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                await run(saver)
        asyncio.run(postgres())


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_information_reply_restores_downstream_dag_without_reexecuting_independent_goal(depth):
    from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
    from application.deterministic_resolution import TurnObservations
    from application.turn_planning import CommandProposal, CommandKind, TurnProposal, ProposalDisposition
    from tests.test_work_recovery import _setup
    from tests.test_target_persistence_and_manager import _identity

    async def run():
        seen = []
        async def worker(context):
            item = context.work_item
            seen.append(item.objective)
            if item.objective == "step0" and not item.continuation_of:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Which choice?"),))
            if item.objective.startswith("step") and item.objective != "step0":
                assert len(context.dependency_results) == 1
                assert context.dependency_results[0].status is AgentResultStatus.SUCCEEDED
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, "DONE", "test")
        manager, _, _ = _setup(worker)
        commands = tuple(CommandProposal(f"s{i}", CommandKind.DELEGATE_TASK, "product_technical", f"step{i}",
            dependencies=(f"s{i-1}",) if i else ()) for i in range(depth + 1))
        independent = CommandProposal("independent", CommandKind.DELEGATE_TASK, "order_logistics", "independent")
        manager._understanding.initial = TurnProposal(ProposalDisposition.RESOLVED, (*commands, independent), "TEST")
        first = await manager.handle(_identity("dag-initial"), TurnObservations("Handle these goals"))
        pending = first.state_after.pending_interaction
        assert len(pending.suspended_work_items) == depth + 1
        assert seen == ["step0", "independent"]
        done = await manager.handle(_identity("dag-resume"), TurnObservations("Choice A",
            interaction_id=pending.interaction_id, interaction_version=pending.version))
        assert done.board.task_completed and done.state_after.pending_interaction is None
        assert seen.count("independent") == 1 and seen.count("step0") == 2
        assert seen[-depth:] == [f"step{i}" for i in range(1, depth + 1)]
    asyncio.run(run())


@pytest.mark.parametrize("questions", [1, 2])
def test_direct_downstream_does_not_turn_a_free_reply_into_verified_slot_values(questions):
    from application.agent_result import RequestedField
    from application.conversation_state import ConversationState, PendingInteractionState
    from application.deterministic_resolution import DeterministicResolver, TurnObservations, ResolutionKind
    from application.target_understanding import StateBoundTargetUnderstanding
    from application.work_item import ControlMode
    from application.turn_planning import CommandKind
    from tests.test_target_framework_agent import _item
    asking = tuple(replace(_item(), work_item_id=f"ask{i}", requirement_ids=()) for i in range(questions))
    direct = replace(asking[0], work_item_id="direct", control_mode=ControlMode.DIRECT,
                     allowed_skills=(), skill_hint=None, dependencies=tuple(item.work_item_id for item in asking))
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    pending = PendingInteractionState("question", 1,
        tuple(RequestedField("reply", item.work_item_id, "string") for item in asking), (), (*asking, direct), "thread")
    state = state.wait_for_interaction(pending)
    observation = TurnObservations("I'm not sure; please explain the options", interaction_id="question", interaction_version=1)
    resolved = DeterministicResolver().resolve(observation, state)
    assert resolved.kind is ResolutionKind.REPLY_PENDING_INPUT and not resolved.fields
    commands = asyncio.run(StateBoundTargetUnderstanding()(observation, state, resolved, None)).commands
    assert commands[-1].kind is CommandKind.DIRECT_TOOL
    assert commands[-1].dependencies == tuple(command.command_id for command in commands[:-1])


def test_controlled_flow_cannot_be_misregistered_as_plain_input_continuation():
    from application.agent_result import RequestedField
    from application.conversation_state import PendingInteractionState, ConversationStateError
    from application.work_item import ControlMode
    from tests.test_target_framework_agent import _item
    flow = replace(_item(), control_mode=ControlMode.WORKFLOW, skill_hint=None, flow_ref="controlled:v1")
    with pytest.raises(ConversationStateError, match="flow or approval continuation"):
        PendingInteractionState("question", 1, (RequestedField("reply", flow.work_item_id, "string"),), (), (flow,), "thread")
