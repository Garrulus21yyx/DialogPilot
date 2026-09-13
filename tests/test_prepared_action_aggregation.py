"""Prepare/barrier/select/approve invariants across producers and persistence."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from application.action_approval import bind_action_approval
from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec, RequestedField
from application.conversation_state import ConversationState, WorkControlStatus
from application.result_board import ResultBoard
from application.work_item import ArgumentValue, WorkControlBinding, WorkPlan
from infrastructure.postgres_target_runtime import conversation_state_to_payload, conversation_state_from_payload
from tests.test_approval_operation_set import prepared


def setup_case(same_target=False, same_origin=False):
    agent, context, produced, _ = prepared(2)
    first = context.work_item
    second = first if same_origin else replace(first, work_item_id="second",
                                               control=WorkControlBinding("second", 1))
    left, right = produced.prepared_actions
    right = replace(right, control=second.control)
    if same_target:
        right = replace(right, arguments=left.arguments, aggregate_ref=left.aggregate_ref)
    if same_origin:
        results = (replace(produced, pending_action=left, additional_actions=(right,)),)
        items = (first,)
    else:
        results = tuple(AgentResult(origin.work_item_id, origin.owner_agent,
            AgentResultStatus.PREPARED, "PREPARED", "test", pending_action=action)
            for action, origin in ((left, first), (right, second)))
        items = (first, second)
    state = ConversationState.empty(tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a")
    state = state.accept_work_items(items, invocation_key="prepare")
    plan = SimpleNamespace(work=SimpleNamespace(items=items))
    board = replace(ResultBoard().evaluate(WorkPlan(items, items[0].work_item_id), results), work_items=items)
    return agent, state, plan, board


def bind(case):
    agent, state, plan, board = case
    return bind_action_approval(state, plan, board, agent._registry, "checkpoint")


def roundtrip(state):
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    restored = conversation_state_from_payload(conversation_state_to_payload(state))
    assert restored == state and restored.fingerprint == state.fingerprint
    serde = target_checkpoint_serializer()
    restored = serde.loads_typed(serde.dumps_typed(restored))
    assert restored == state
    return restored


def select(state, value="1", extra=()):
    pending = state.pending_interaction
    field = next(field for field in pending.requested_fields if field.field_name == "prepared_alternative")
    values = ((field.target_work_item_id, field.field_name, value), *extra)
    return state.consume_interaction(interaction_id=pending.interaction_id,
                                    interaction_version=pending.version, values=values)


def test_different_origins_share_one_exact_approval_without_repreparing():
    case = setup_case()
    state = roundtrip(bind(case))
    assert len(state.pending_approval.operations) == 2
    assert len(state.pending_approval.origin_controls) == 2
    assert state.pending_interaction is None
    assert not state.accepted_approvals


@pytest.mark.parametrize("same_origin", [False, True])
def test_incompatible_candidates_choose_before_approval_and_retain_operation_exclusion(same_origin):
    case = setup_case(same_target=True, same_origin=same_origin)
    state = roundtrip(bind(case))
    assert state.pending_approval is None
    assert len(state.pending_interaction.approval_options) == 2
    selected = roundtrip(select(state))
    assert selected.pending_interaction is None
    assert len(selected.pending_approval.operations) == 1
    assert len(selected.excluded_preparations) == 1
    assert selected.accepted_approvals == ()  # choosing is not execution consent
    if same_origin:
        agent, _, _, _ = case
        origin = selected.pending_approval.suspended_work_items[0]
        excluded = selected.excluded_preparations[0]
        from infrastructure.target_action_preparation import TargetActionPreparation
        from tests.test_approval_conversation import domain
        _, context, _, _ = domain([])
        resumed = replace(origin, work_item_id="continued", continuation_of=origin.work_item_id,
                          control=WorkControlBinding(origin.control.control_id, 2))
        context = replace(context, work_item=resumed, trusted_context={
            **context.trusted_context, "excluded_preparations": selected.excluded_preparations})
        result = asyncio.run(TargetActionPreparation(agent._registry, agent._tool_manager).prepare(
            context, excluded.action_ref, {arg.name: arg.value for arg in excluded.arguments}, "new-call"))
        assert result.reason_code == "ACTION_EXCLUDED_BY_USER_CHOICE"


def test_related_missing_input_holds_candidate_and_retained_outcome_finishes_barrier():
    agent, state, plan, board = setup_case()
    first, second = plan.work.items
    second = replace(second, arguments=(ArgumentValue.create("order_id", "DP1000"),))
    # Still same control: this is a prepared execution envelope, not new admission.
    missing = AgentResult(second.work_item_id, second.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
        "MISSING", "test", missing_inputs=(MissingInputSpec("reply", second.work_item_id, "MISSING", "string", "Which option?"),))
    held = replace(board, work_items=(first, second), results=(board.results[0], missing))
    state = bind_action_approval(state, SimpleNamespace(work=SimpleNamespace(items=(first, second))), held, agent._registry, "checkpoint")
    assert state.pending_approval is None
    # After answering, the previous candidate comes from the checkpoint rather
    # than a new preparation call; unrelated current output must not erase it.
    recovered = replace(board, work_items=(second,), results=(board.results[1],),
                        retained_outcomes=((first, board.results[0]),))
    state = bind_action_approval(state, SimpleNamespace(work=SimpleNamespace(items=(second,))), recovered, agent._registry, "checkpoint")
    assert len(state.pending_approval.operations) == 2


@pytest.mark.parametrize("answer_both", [False, True])
def test_selection_and_independent_fields_share_one_interaction(answer_both):
    agent, original, plan, board = setup_case(same_target=True)
    state = bind((agent, original, plan, board))
    independent = replace(plan.work.items[0], work_item_id="independent",
                          control=WorkControlBinding("independent", 1), allowed_actions=())
    state = state.accept_work_items((independent,), invocation_key="independent")
    pending = state.pending_interaction
    state = state.extend_interaction(replace(pending, version=pending.version + 1,
        requested_fields=(*pending.requested_fields, RequestedField("color", "independent", "string", "Which color?")),
        suspended_work_items=(*pending.suspended_work_items, independent)))
    state = roundtrip(state)
    state = select(state, extra=(("independent", "color", "blue"),) if answer_both else ())
    assert state.pending_approval
    assert (state.pending_interaction is None) == answer_both
    assert state.accepts_work(independent)
    roundtrip(state)


def test_independent_approval_does_not_occupy_slot_a_choice_needs():
    agent, state, plan, board = setup_case(same_target=True)
    independent = replace(plan.work.items[0], work_item_id="independent",
                          control=WorkControlBinding("independent", 1))
    state = state.accept_work_items((independent,), invocation_key="independent")
    action = replace(board.results[0].pending_action, operation_key="independent-op", control=independent.control,
                     arguments=(ArgumentValue.create("order_id", "OTHER"), ArgumentValue.create("expected_order_version", 4)))
    result = AgentResult(independent.work_item_id, independent.owner_agent, AgentResultStatus.PREPARED,
                         "PREPARED", "test", pending_action=action)
    board = replace(board, work_items=(*board.work_items, independent), results=(*board.results, result))
    state = bind_action_approval(state, SimpleNamespace(work=SimpleNamespace(items=board.work_items)), board, agent._registry, "checkpoint")
    assert state.pending_approval is None
    assert all(len(option.operations) == 2 for option in state.pending_interaction.approval_options)
    selected = select(roundtrip(state))
    assert len(selected.pending_approval.operations) == 2


@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_parallel_preparations_restart_and_resume_only_missing_worker(backend, request):
    from contextlib import AsyncExitStack
    from langgraph.checkpoint.memory import InMemorySaver
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer, AsyncPostgresCheckpointOwner
    from application.orchestration_runtime import OrchestrationRuntime
    database = request.getfixturevalue("postgres_database_url") if backend == "postgres" else None
    agent, state, plan, board = setup_case()
    first, second = plan.work.items
    calls = []

    async def run():
        nonlocal state
        both = asyncio.Event()

        async def worker(context):
            work = context.work_item
            calls.append(work.work_item_id)
            if len(calls) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), timeout=2)
            if work.work_item_id == first.work_item_id:
                return board.results[0]
            if work.work_item_id == second.work_item_id:
                return AgentResult(work.work_item_id, work.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "MISSING", "test", missing_inputs=(MissingInputSpec("order_id", work.work_item_id,
                        "MISSING", "string", "Which order?"),))
            return replace(board.results[1], work_item_id=work.work_item_id,
                           pending_action=replace(board.results[1].pending_action, control=work.control))

        async with AsyncExitStack() as stack:
            saver = (await stack.enter_async_context(AsyncPostgresCheckpointOwner(database, setup=True))
                     if database else InMemorySaver(serde=target_checkpoint_serializer()))
            runtime = OrchestrationRuntime(direct_executor=worker,
                domain_workers={first.owner_agent: worker}, checkpointer=saver)
            work_plan = WorkPlan((first, second), first.work_item_id)
            before = await runtime.execute(work_plan, thread_id="parallel-preparation", current_message="do both")
            assert len(calls) == 2  # the event proves overlapping execution
            held = bind_action_approval(state, SimpleNamespace(work=work_plan), before, agent._registry, "parallel-preparation")
            assert held.pending_approval is None
            # New runtime instance, same native durable graph; retained candidates
            # must remain available without rerunning their worker.
            resumed = replace(second, work_item_id="resumed-second", continuation_of=second.work_item_id,
                              control=WorkControlBinding(second.control.control_id, 2))
            state = state.accept_work_items((resumed,), invocation_key="answer")
            runtime = OrchestrationRuntime(direct_executor=worker,
                domain_workers={first.owner_agent: worker}, checkpointer=saver)
            resumed_plan = WorkPlan((resumed,), resumed.work_item_id)
            after = await runtime.resume(resumed_plan, thread_id="parallel-preparation", current_message="DP1001")
            state = bind_action_approval(state, SimpleNamespace(work=resumed_plan), after, agent._registry, "parallel-preparation")
            assert len(state.pending_approval.operations) == 2
            assert calls.count(first.work_item_id) == 1
            assert calls == [first.work_item_id, second.work_item_id, resumed.work_item_id]
    asyncio.run(run())


def test_cancel_choice_participant_preserves_independent_question():
    agent, initial, plan, board = setup_case(same_target=True)
    state = bind((agent, initial, plan, board))
    work = replace(plan.work.items[0], work_item_id="other", control=WorkControlBinding("other", 1), allowed_actions=())
    state = state.accept_work_items((work,), invocation_key="other")
    pending = state.pending_interaction
    state = state.extend_interaction(replace(pending, version=pending.version + 1,
        requested_fields=(*pending.requested_fields, RequestedField("reply", "other", "string", "Which color?")),
        suspended_work_items=(*pending.suspended_work_items, work)))
    state = state.close_work_control(plan.work.items[0].control,
        status=WorkControlStatus.CANCELLED)
    assert not state.pending_interaction.approval_options
    assert [field.target_work_item_id for field in state.pending_interaction.requested_fields] == ["other"]


def test_repeated_failed_preparation_retries_have_fresh_selectable_decisions():
    agent, state, plan, board = setup_case()
    first, failed = plan.work.items
    failed = replace(failed, arguments=first.arguments)  # unresolved target stays related
    seen = set()
    for revision in range(1, 4):
        failure = AgentResult(failed.work_item_id, failed.owner_agent, AgentResultStatus.RETRYABLE_FAILURE,
                              "DEPENDENCY_UNAVAILABLE", "test", retryable=True)
        current = replace(board, work_items=(first, failed), results=(board.results[0], failure))
        state = bind_action_approval(state, SimpleNamespace(work=SimpleNamespace(items=(first, failed))),
                                    current, agent._registry, "checkpoint")
        assert state.pending_approval is None
        signal = (state.pending_interaction.interaction_id, state.pending_interaction.version)
        assert signal not in seen
        seen.add(signal)
        state = roundtrip(select(state, "retry" if revision < 3 else "1"))
        if revision < 3:
            failed = replace(failed, work_item_id=f"retry-{revision}", continuation_of=failed.work_item_id,
                             control=WorkControlBinding(failed.control.control_id, revision + 1))
            state = state.accept_work_items((failed,), invocation_key=f"retry-{revision}")
    assert len(state.pending_approval.operations) == 1
    assert state.accepted_approvals == ()


@pytest.mark.parametrize("new_goal", [False, True])
def test_exclusion_survives_continuation_but_retires_for_explicit_new_goal(new_goal):
    case = setup_case(same_target=True, same_origin=True)
    state = select(bind(case))
    original = case[2].work.items[0]
    revised = replace(original, work_item_id="next", control=WorkControlBinding(original.control.control_id, 2),
                      continuation_of=None if new_goal else original.work_item_id)
    state = state.accept_work_items((revised,), invocation_key="next")
    resumed = replace(revised, work_item_id="answer", control=WorkControlBinding(original.control.control_id, 3),
                      continuation_of=revised.work_item_id)
    state = roundtrip(state.accept_work_items((resumed,), invocation_key="answer"))
    assert bool(state.excluded_preparations) is not new_goal


def test_business_dependency_stages_approval_and_preserves_unstarted_descendants():
    agent, state, plan, board = setup_case()
    first, second = plan.work.items
    second = replace(second, dependencies=(first.work_item_id,))
    work = WorkPlan((first, second), first.work_item_id)
    board = ResultBoard().evaluate(work, (board.results[0],))
    assert board.blocked_results[0].work_item_id == second.work_item_id
    state = roundtrip(bind_action_approval(state, SimpleNamespace(work=work), board, agent._registry, "checkpoint"))
    assert state.pending_interaction is None
    assert len(state.pending_approval.operations) == 1
    assert state.pending_approval.suspended_work_items == (first, second)
    from application.deterministic_resolution import DeterministicResolver, TurnObservations
    from application.target_understanding import StateBoundTargetUnderstanding
    pending = state.pending_approval
    observation = TurnObservations("", approval_decision=True, approval_id=pending.approval_id)
    resolution = DeterministicResolver().resolve(observation, state)
    state = state.consume_approval(approval_id=pending.approval_id, approval_version=pending.version, approved=True)
    proposal = asyncio.run(StateBoundTargetUnderstanding()(observation, state, resolution, agent._registry))
    action, first_continuation, second_continuation = proposal.commands
    assert action.command_id in first_continuation.dependencies
    assert first_continuation.command_id in second_continuation.dependencies


def test_batch_preparation_requires_every_requested_candidate_but_keeps_successes():
    from langchain_core.messages import AIMessage
    from tests.test_approval_conversation import domain
    agent, context, model, calls = domain([AIMessage(content="", tool_calls=[
        {"name": "prepare_order_cancel", "args": {"order_id": "GOOD"}, "id": "good"},
        {"name": "prepare_order_cancel", "args": {"order_id": "BAD"}, "id": "bad"},
    ]), AIMessage(content="", tool_calls=[{"name": "report_blocked", "id": "stop",
          "args": {"reason": "The second order cannot be prepared."}}])])
    tool = next(tool for tool in agent._tool_manager.registered_tools if tool.name == "order_lookup")
    async def read(arguments, runtime_context):
        calls.append(arguments["order_id"])
        return {"order_id": arguments["order_id"], "status": "paid" if arguments["order_id"] == "GOOD" else "cancelled", "version": 4}
    agent._tool_manager.register(replace(tool, handler=read))
    result = asyncio.run(agent(context))
    assert not result.prepared_actions
    assert result.status is AgentResultStatus.BLOCKED
    assert any(entry["data"].get("artifact", {}).get("result", {}).get("pending_action")
               for entry in result.working_messages if entry["type"] == "tool")
    assert sorted(calls) == ["BAD", "GOOD"]


@pytest.mark.parametrize("same_target", [False, True])
def test_cross_domain_candidates_use_resource_identity_not_agent_names(same_target):
    agent, state, plan, board = setup_case(same_target=same_target)
    registry = agent._registry
    first, second = plan.work.items
    specialist = replace(registry.agents[0], agent_id="second-specialist")
    action = replace(registry.actions[0], action_id="second.change", owner_agent=specialist.agent_id)
    registry = replace(registry, agents=(*registry.agents, specialist), actions=(*registry.actions, action))
    first = replace(first, registry_fingerprint=registry.fingerprint)
    second = replace(second, owner_agent=specialist.agent_id, allowed_actions=(action.ref,),
                     registry_fingerprint=registry.fingerprint)
    left = replace(board.results[0], pending_action=replace(board.results[0].pending_action,
                   registry_fingerprint=registry.fingerprint))
    right = replace(board.results[1], owner_agent=specialist.agent_id,
        pending_action=replace(board.results[1].pending_action, action_ref=action.ref,
            owner_agent=specialist.agent_id, registry_fingerprint=registry.fingerprint))
    state = ConversationState.empty(tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a")
    state = state.accept_work_items((first, second), invocation_key="prepare")
    work = WorkPlan((first, second), first.work_item_id)
    state = roundtrip(bind_action_approval(state, SimpleNamespace(work=work),
        ResultBoard().evaluate(work, (left, right)), registry, "checkpoint"))
    if same_target:
        assert state.pending_approval is None
        state = select(state)
    assert len(state.pending_approval.operations) == (1 if same_target else 2)
    from tests.test_approval_origin_lineage import approve, compile_plan
    approved, proposal = approve(registry, state)
    compiled = compile_plan(registry, approved, proposal)
    writes = [item for item in compiled.work.items if item.operation_key]
    assert {item.operation_key for item in writes} == {op.operation_key for op in state.pending_approval.operations}
    assert all(item.owner_agent == registry.action(item.action_ref).owner_agent for item in writes)


def test_selected_candidate_executes_once_after_postgres_restart(postgres_database_url):
    from uuid import uuid4
    from infrastructure.postgres import PostgresPool, PostgresPoolConfig, PostgresMigrationRunner
    from infrastructure.target_workflow_execution import TargetWorkflowExecutor
    from mcp.tool_manager import ToolEffectReceipt, ToolEffectStatus
    from application.conversation_store import ConversationScope
    from core.identity import TenantId, UserId, ConversationId
    from tests.test_write_workflow import accept_work
    from tests.test_target_framework_agent import _context
    agent, state, plan, board = setup_case(same_target=True)
    state = roundtrip(select(bind((agent, state, plan, board))))
    pending = state.pending_approval
    state = state.consume_approval(approval_id=pending.approval_id, approval_version=pending.version, approved=True)
    chosen = next(result.pending_action for result in board.results
                  if result.pending_action.operation_key == pending.operation_key)
    writes = []
    async def write(arguments, runtime_context):
        writes.append(arguments["order_id"])
        return ToolEffectReceipt({"order_id": arguments["order_id"], "cancelled": True},
                                 ToolEffectStatus.COMMITTED, "chosen-receipt")
    tool = next(tool for tool in agent._tool_manager.registered_tools if tool.name == "order_cancel")
    agent._tool_manager.register(replace(tool, handler=write, requires_approval=True,
        receipt_schema_version="action-receipt-v1", schema={**tool.schema,
            "properties": {**tool.schema["properties"], "expected_order_version": {"type": "integer"}}}))
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
    pool.open()
    conversation = "selected-" + uuid4().hex
    context = _context(replace(chosen, approval_binding=pending.approval_id))
    context = replace(context, trusted_context={**context.trusted_context, "conversation_id": conversation,
        "approval_binding": pending.approval_id, "approved_operations": [op.view() for op in pending.operations]})
    async def run():
        first = TargetWorkflowExecutor(pool, agent._tool_manager, registry=agent._registry)
        done = await first(context)
        assert done.status is AgentResultStatus.SUCCEEDED
        fresh = TargetWorkflowExecutor(pool, agent._tool_manager, registry=agent._registry)
        replayed = await fresh(context)
        assert replayed.action_receipts == done.action_receipts
        assert len(writes) == 1
    try:
        scope = ConversationScope(TenantId(context.trusted_context["tenant_id"]),
            UserId(context.trusted_context["user_id"]), ConversationId(conversation))
        accept_work(pool, scope, pending.suspended_work_items[0])
        asyncio.run(run())
    finally:
        pool.close()


def test_undelivered_choice_is_composed_from_saved_alternatives_without_execution():
    from application.preparation_selection import choice_context
    from application.response_assembly import ResponseAssembler
    from application.turn_runtime import TurnRuntime
    from tests.test_response_assembly import _Composer
    from tests.test_knowledge_answer_boundary import Verifier
    case = setup_case(same_target=True)
    state = roundtrip(bind(case))
    composer = _Composer("These changes cannot both proceed. Which one would you like to keep?")
    assembler = ResponseAssembler(composer, knowledge_verifier=Verifier(True), fallback_locale="en")
    runtime = TurnRuntime(None, assembler, interaction_published=lambda *args, **kwargs: False)
    managed = SimpleNamespace(board=None, state_after=state, state_before=state,
        interaction_questions=(), diagnostics=(), request_completed=False,
        plan=SimpleNamespace(response_text=None, route=SimpleNamespace(reason_code="CLARIFY", missing_inputs=())))
    output = asyncio.run(runtime._assemble_response({"managed": managed, "presentation_state": state,
        "invocation": object(), "prepared": SimpleNamespace(context=None),
        "observations": SimpleNamespace(raw_text="What are my options?")}))
    assert len(composer.calls) == 1
    evidence = composer.calls[0]["evidence"]
    assert evidence["preparation_choice"] == choice_context(state.pending_interaction)
    assert evidence["pending_actions"] == []
    assert output["assembled"].text == composer.response
    assert output["assembled"].interaction_ready
    assert not output["assembled"].approval_operation_key


def test_planner_receives_saved_choice_mapping_as_runtime_data_not_user_authorization():
    import json
    from application.conversation_agent import ConversationAgent
    from application.deterministic_resolution import DeterministicResolver, TurnObservations
    from application.target_conversation_manager import TargetTurnContext
    from application.preparation_selection import choice_context
    from infrastructure.target_model_context import planning_context
    from tests.test_conversation_agent import Provider
    case = setup_case(same_target=True)
    state = roundtrip(bind(case))
    provider = Provider({"status": "respond", "response": "You can choose one of these alternatives."})
    observations = TurnObservations("What are my options?")
    resolution = DeterministicResolver().resolve(observations, state)
    asyncio.run(ConversationAgent(provider).plan(observations, state, resolution,
                                               case[0]._registry, TargetTurnContext()))
    contract, messages = planning_context(provider.calls[0])
    runtime = json.loads(messages[-1].content[0]["text"])["runtime_context"]
    assert runtime["preparation_choice"] == choice_context(state.pending_interaction)
    assert runtime["pending_approval"] is None
    assert "preparation_choice" not in contract
    assert json.loads(messages[-1].content[1]["text"]) == {"current_request": observations.raw_text}


def test_choice_without_native_checkpoint_uses_the_same_persisted_decision_contract():
    agent, state, plan, board = setup_case(same_target=True)
    state = roundtrip(bind_action_approval(state, plan, board, agent._registry, None))
    state = roundtrip(select(state))
    assert state.pending_approval.checkpoint_thread_id is None
    assert len(state.pending_approval.operations) == 1


@pytest.mark.parametrize("status", [AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL])
def test_uncovered_business_outcome_cannot_satisfy_preparation_barrier(status):
    agent, state, plan, board = setup_case()
    first, second = plan.work.items
    second = replace(second, requirement_ids=("order.cancel_action",))
    incomplete = AgentResult(second.work_item_id, second.owner_agent, status, "NO_RECEIPT", "test")
    work = WorkPlan((first, second), first.work_item_id)
    board = ResultBoard().evaluate(work, (board.results[0], incomplete))
    assert not board.coverage_for(second, incomplete)["task_completed"]
    state = bind_action_approval(state, SimpleNamespace(work=work), board, agent._registry, "checkpoint")
    assert state.pending_approval is None
    assert state.pending_interaction.approval_options
