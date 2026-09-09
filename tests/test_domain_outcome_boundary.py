"""Outcome/goal algebra on the real SDK graph; semantic judgments are scripted."""
import asyncio
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from tests.test_approval_conversation import domain, call


@pytest.mark.parametrize('succeeded', [False, True])
@pytest.mark.parametrize('historical', [False, True])
@pytest.mark.parametrize('accepted', [False, True])
def test_segment_terminal_follows_successful_preparation_not_review_or_history(succeeded, historical, accepted):
    from types import SimpleNamespace
    from infrastructure.target_agent_middleware import InteractionBoundaryMiddleware
    context = SimpleNamespace(working_messages=({'type': 'tool', 'data': {'tool_call_id': 'p'}},) if historical else ())
    state = {'messages': [], 'tool_observations': {'p': {'pending_action': {'id': 'proposal'} if succeeded else None}},
             'accepted_outcome': {'kind': 'PREPARE_ACTION'} if accepted else {}}
    middleware = InteractionBoundaryMiddleware(('action-A', 'action-B'), review=None)
    update = asyncio.run(middleware.abefore_model(state, SimpleNamespace(context=context)))
    assert update == ({'jump_to': 'end'} if succeeded and not historical else None)


def terminal(tool, text, ident="terminal"):
    return AIMessage(content="", tool_calls=[{"name": tool, "id": ident,
        "args": {"question" if tool == "request_user_input" else "reason": text}}])


def test_assignment_error_returns_to_planner_without_local_retry_or_preparation():
    agent, context, model, calls = domain([call('prepare_order_cancel'), AIMessage(content='must not run')])
    context = replace(context, trusted_context={**context.trusted_context, 'assignment_view': {
        'assignments': [{'work_item_id': context.work_item.work_item_id, 'objective': 'Only identify user'}]}})
    model.outcome_reviews = [{'accepted': False, 'feedback': 'The request includes a return, not only identification.',
        'repair_owner': 'conversation'}]
    result = asyncio.run(agent(context))
    assert result.status.value == 'TERMINAL_FAILURE'
    assert result.assignment_issue == model.outcome_reviews[0]['feedback']
    assert not result.retryable and not result.pending_action and not calls
    assert model.calls == model.review_calls == 1
    assert result.execution_feedback


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
    assert model.calls == 2 and model.review_calls == 2
    assert [entry["accepted"] for entry in result.execution_feedback
            if entry["stage"] == "domain_outcome"] == [False, True]
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


@pytest.mark.parametrize("next_step", [
    call("order_lookup", "after-prepare"),
    call("prepare_order_cancel", "duplicate"),
    terminal("request_user_input", "Repeat the choice"),
    terminal("report_blocked", "New condition"),
    AIMessage(content="Another final response"),
])
@pytest.mark.parametrize("prior_commit", [False, True])
def test_preparation_is_segment_terminal_preserving_pending_goal_and_prior_receipts(next_step, prior_commit):
    from application.agent_result import AgentResult, AgentResultStatus, ReceiptRef
    agent, context, model, executed = domain([call("prepare_order_cancel"), next_step])
    prior = AgentResult("independent", context.work_item.owner_agent, AgentResultStatus.SUCCEEDED,
        "COMMITTED", "test", action_receipts=(ReceiptRef("receipt", "v1", "earlier-operation",
                                                    "COMMITTED", "order.address_action"),))
    if prior_commit:
        context = replace(context, dependency_results=(prior,))
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL"
    assert result.pending_action.objective == context.work_item.objective
    assert result.pending_action.arguments and result.facts
    assert result.candidate_response is None and not result.action_receipts
    assert len(executed) == model.calls == model.review_calls == 1
    assert context.dependency_results == ((prior,) if prior_commit else ())
    assert not result.missing_inputs


def test_preparation_archive_failure_preserves_proposal_and_stops_without_retry():
    from langgraph.store.memory import InMemoryStore
    from infrastructure.target_result_archive import TargetResultArchive

    class Unavailable(InMemoryStore):
        async def aput(self, *args, **kwargs):
            raise OSError("storage unavailable")

    agent, context, model, executed = domain([
        call("prepare_order_cancel"), call("prepare_order_cancel", "must-not-retry")])
    agent._archive = TargetResultArchive(Unavailable())
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL"
    assert result.pending_action.objective == context.work_item.objective
    assert result.pending_action.arguments and result.facts
    assert result.candidate_response is None and not result.action_receipts
    assert len(executed) == model.calls == model.review_calls == 1
    assert any(entry.get("stage") == "agent_result_archive" for entry in result.execution_feedback)
    tools = [entry["data"] for entry in result.working_messages if entry["type"] == "tool"]
    assert any((entry.get("artifact") or {}).get("result", {}).get("pending_action") for entry in tools)


def test_host_resumed_segment_does_not_republish_historical_preparation():
    from application.work_item import WorkControlBinding
    agent, context, model, executed = domain([
        call("prepare_order_cancel"),
        AIMessage(content="The revised objective needs no further action."),
    ])
    context = replace(context, work_item=replace(context.work_item, control=WorkControlBinding("goal", 1)))
    first = asyncio.run(agent(context))
    assert first.status.value == "WAITING_APPROVAL"
    # Host has cancelled/revised the pending approval, then provided a new segment.
    resumed = replace(context, work_item=replace(context.work_item,
                      work_item_id="continued", continuation_of=context.work_item.work_item_id,
                      objective="Keep the order unchanged", allowed_actions=(),
                      control=WorkControlBinding("goal", 2)),
                      working_messages=first.working_messages,
                      verified_facts=first.facts, current_message="Keep the order unchanged")
    second = asyncio.run(agent(resumed))
    assert second.status.value == "SUCCEEDED" and second.pending_action is None
    assert not second.missing_inputs and not second.action_receipts
    assert len(executed) == 1 and model.calls == 2


@pytest.mark.parametrize('read_first', [False, True])
def test_terminal_preparation_waits_for_complete_parallel_batch(read_first):
    reads = call('order_lookup', 'independent-read').tool_calls
    proposal = call('prepare_order_cancel', 'proposal').tool_calls
    agent, context, model, executed = domain([AIMessage(content='',
        tool_calls=reads + proposal if read_first else proposal + reads)])
    result = asyncio.run(agent(context))
    assert result.status.value == 'WAITING_APPROVAL' and result.pending_action
    assert len(executed) == 2 and model.calls == 1
    assert any(fact.source_ref == 'independent-read' for fact in result.facts)
    assert not result.action_receipts and result.candidate_response is None


def test_failed_preparation_does_not_end_segment_or_turn_acceptance_into_success():
    agent, context, model, executed = domain([call('prepare_order_cancel', 'first'),
        call('prepare_order_cancel', 'corrected')])
    tool = next(tool for tool in agent._tool_manager.registered_tools if tool.name == 'order_lookup')
    original = tool.handler
    attempts = []
    async def initially_not_ready(params, ctx):
        data = await original(params, ctx)
        attempts.append(params)
        return {**data, 'status': 'not_ready'} if len(attempts) == 1 else data
    tool.handler = initially_not_ready
    result = asyncio.run(agent(context))
    assert result.status.value == 'WAITING_APPROVAL'
    assert result.pending_action.work_item_id.endswith(':action:corrected')
    assert len(executed) == model.calls == model.review_calls == 2
    assert not result.action_receipts


def test_preparation_failure_does_not_spend_the_semantic_correction():
    agent, context, model, executed = domain([
        call('prepare_order_cancel', 'not-ready'),
        terminal('report_blocked', 'An earlier preparation failed.'),
        call('prepare_order_cancel', 'ready')])
    model.outcome_reviews = [
        {'accepted': True, 'feedback': ''},
        {'accepted': False, 'feedback': 'The preparation failure is recoverable; reassess the action.'},
        {'accepted': True, 'feedback': ''}]
    tool = next(tool for tool in agent._tool_manager.registered_tools if tool.name == 'order_lookup')
    original = tool.handler
    attempts = []
    async def initially_not_ready(params, ctx):
        data = await original(params, ctx)
        attempts.append(params)
        return {**data, 'status': 'not_ready'} if len(attempts) == 1 else data
    tool.handler = initially_not_ready
    result = asyncio.run(agent(context))
    assert result.status.value == 'WAITING_APPROVAL'
    assert result.pending_action.work_item_id.endswith(':action:ready')
    assert model.calls == model.review_calls == 3
    assert len(executed) == 2 and not result.action_receipts


@pytest.mark.parametrize('accepted_history', [0, 1, 2, 5])
@pytest.mark.parametrize('rejected_history', [0, 1, 2])
def test_correction_budget_depends_on_rejections_not_accepted_reviews(accepted_history, rejected_history):
    from types import SimpleNamespace
    from infrastructure.target_agent_middleware import InteractionBoundaryMiddleware
    from infrastructure.target_domain_outcome import DomainOutcomeRejected
    calls = []
    class Review:
        async def assess(self, **kwargs):
            calls.append(kwargs)
            return {'accepted': False, 'feedback': 'The assigned task remains incomplete.'}
    state = {'messages': [AIMessage('Done.')],
             'outcome_review_calls': accepted_history + rejected_history,
             'outcome_feedback': ([{'accepted': True, 'feedback': ''}] * accepted_history
                                  + [{'accepted': False, 'feedback': 'Incomplete.'}] * rejected_history)}
    boundary = InteractionBoundaryMiddleware(review=Review())
    if rejected_history:
        with pytest.raises(DomainOutcomeRejected):
            asyncio.run(boundary.aafter_model(state, SimpleNamespace(context=None)))
        assert len(calls) == (1 if rejected_history == 1 else 0)
    else:
        update = asyncio.run(boundary.aafter_model(state, SimpleNamespace(context=None)))
        assert update['jump_to'] == 'model'
        assert update['outcome_review_calls'] == accepted_history + 1


@pytest.mark.parametrize("batch", ["single", "read_first", "read_last"])
@pytest.mark.parametrize("reason", ["wrong objective", "wrong target", "already completed action"])
def test_rejected_action_selection_never_prepares_or_executes_its_batch(batch, reason, monkeypatch):
    from infrastructure.target_domain_outcome import DomainOutcomeReview
    seen = []
    original = DomainOutcomeReview.assess

    async def inspect(self, **kwargs):
        seen.append((kwargs["kind"], kwargs["candidate"]))
        return await original(self, **kwargs)

    monkeypatch.setattr(DomainOutcomeReview, "assess", inspect)
    rejected = call("prepare_order_cancel", "rejected").tool_calls
    rejected[0]["args"] = {"order_id": "OTHER"}
    read = call("order_lookup", "unexecuted-read").tool_calls
    batch_calls = (read + rejected if batch == "read_first" else
                   rejected + read if batch == "read_last" else rejected)
    agent, context, model, executed = domain([
        AIMessage(content="", tool_calls=batch_calls), call("prepare_order_cancel", "corrected"),
        AIMessage(content="The requested cancellation is prepared, not executed.")])
    model.outcome_reviews = [{"accepted": False, "feedback": reason},
                             {"accepted": True, "feedback": ""}]
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL"
    assert result.pending_action.work_item_id.endswith(":action:corrected")
    assert executed == [("read", {"order_id": "DP1234"})]
    assert model.review_calls == 2 and model.calls == 2
    assert seen == [("PREPARE_ACTION", {"tool": "prepare_order_cancel", "arguments": {"order_id": "OTHER"}}),
                    ("PREPARE_ACTION", {"tool": "prepare_order_cancel", "arguments": {"order_id": "DP1234"}})]
    errors = {entry["data"]["tool_call_id"] for entry in result.working_messages
              if entry["type"] == "tool" and entry["data"].get("status") == "error"}
    assert errors == {item["id"] for item in batch_calls}
    assert not result.action_receipts


def test_two_rejected_preparations_exhaust_shared_budget_without_pending_state():
    agent, context, model, executed = domain([
        call("prepare_order_cancel", "first"), call("prepare_order_cancel", "second")])
    model.outcome_reviews = [{"accepted": False, "feedback": "This action is outside the assigned objective."}] * 2
    result = asyncio.run(agent(context))
    assert result.reason_code == "DOMAIN_OUTCOME_REJECTED"
    assert not result.pending_action and not result.action_receipts and not executed
    assert model.review_calls == 2


def test_accepted_distinct_action_keeps_existing_receipts_and_does_not_request_extra_review():
    from application.agent_result import AgentResult, AgentResultStatus, ReceiptRef
    agent, context, model, executed = domain([
        call("prepare_order_cancel"), AIMessage(content="Cancellation prepared, not executed.")])
    prior = AgentResult("prior", context.work_item.owner_agent, AgentResultStatus.SUCCEEDED,
        "COMMITTED", "test", action_receipts=(ReceiptRef("receipt", "v1", "earlier-operation",
                                                    "COMMITTED", "order.address_action"),))
    context = replace(context, dependency_results=(prior,))
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL"
    assert result.pending_action.operation_key != "earlier-operation"
    assert context.dependency_results == (prior,)
    assert model.review_calls == 1 and len(executed) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_current_proposal_advertisement_matches_exposed_tools(pending):
    import json
    from application.conversation_state import PendingApprovalState
    agent, context, _, _ = domain([])
    if pending:
        context = replace(context, pending_approval=PendingApprovalState(
            "approval", 1, "stream", "action", "order.cancel:v1", "operation",
            "order:OTHER", "1", "2099-01-01T00:00:00+00:00"))
    advertised = json.loads(agent._build_prompt(context)[-1]["text"])["delegated_task"]["action_proposals_allowed"]
    exposed = {tool.name for tool in agent._tools(context)}
    assert advertised is (not pending)
    assert ("prepare_order_cancel" in exposed) is advertised


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
    agent._review_model = agent._model
    result = asyncio.run(agent(context))
    assert result.reason_code == "DOMAIN_OUTCOME_REVIEW_UNAVAILABLE"
    assert result.retryable is retryable


@pytest.mark.parametrize("budget", [1, 14200])
def test_review_uses_its_own_model_and_budget_without_actor_fallback(budget):
    from tests.test_target_framework_agent import ScriptedToolModel
    agent, context, actor, calls = domain([
        call("prepare_order_cancel", "wrong"), AIMessage(content="No further action is necessary.")])
    reviewer = ScriptedToolModel(responses=[], outcome_reviews=[
        {"accepted": False, "feedback": "The recorded action is already completed; summarize it."},
        {"accepted": True, "feedback": ""}])
    agent._review_model = reviewer
    agent._review_available_tokens = budget
    result = asyncio.run(agent(context))
    assert actor.review_calls == 0 and not calls and result.pending_action is None
    if budget == 1:
        assert reviewer.review_calls == 0 and actor.calls == 0
        assert result.reason_code == "CONTEXT_BUDGET_EXCEEDED"
        assert result.retryable is False
    else:
        assert reviewer.review_calls == 2 and actor.calls == 2
        assert result.status.value == "SUCCEEDED"


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
    from tests.test_task_result_lifecycle import _setup
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
    assert asyncio.run(StateBoundTargetUnderstanding()(observation, state, resolved, None)) is None
    # Dependency preservation remains an owner operation after semantic choice,
    # not an automatic consequence of a correlated free-text message.
    commands = StateBoundTargetUnderstanding._continuations(pending.suspended_work_items, state)
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
