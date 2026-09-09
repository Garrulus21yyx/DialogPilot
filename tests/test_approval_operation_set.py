"""Properties of one displayed consent scope with independent operation members."""
import asyncio
import itertools
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from application.action_approval import bind_action_approval
from application.conversation_state import ConversationState
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.response_assembly import ResponseAssembler
from application.target_understanding import StateBoundTargetUnderstanding
from application.turn_planning import RoutePolicy, TurnPlanCompiler
from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
from tests.test_approval_conversation import domain
from tests.test_response_assembly import _board


def prepared(count):
    calls = [{"name": "prepare_order_cancel", "args": {"order_id": f"DP{1000+i}"},
              "id": f"prepare-{i}"} for i in range(count)]
    agent, context, model, reads = domain([AIMessage(content="", tool_calls=calls)])
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL", result
    assert len(result.prepared_actions) == len(reads) == count
    assert model.calls == model.review_calls == 1
    state = ConversationState.empty(tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a")
    state = bind_action_approval(state, SimpleNamespace(work=SimpleNamespace(items=(context.work_item,))),
        _board(result), agent._registry, "checkpoint")
    return agent, context, result, state


@pytest.mark.parametrize("count", [1, 2, 4])
def test_every_prepared_member_survives_state_approval_and_compilation(count):
    agent, context, result, state = prepared(count)
    restored = conversation_state_from_payload(conversation_state_to_payload(state))
    assert restored == state and restored.fingerprint == state.fingerprint
    pending = restored.pending_approval
    resolution = DeterministicResolver().resolve(
        TurnObservations("", approval_decision=True, approval_id=pending.approval_id), restored)
    approved = restored.consume_approval(approval_id=pending.approval_id,
        approval_version=pending.version, approved=True)
    approved = conversation_state_from_payload(conversation_state_to_payload(approved))
    proposal = asyncio.run(StateBoundTargetUnderstanding()(TurnObservations("yes"), approved, resolution, agent._registry))
    writes = proposal.commands[:count]
    from core.identity import IdentityFactory
    identity = IdentityFactory(lambda: "scope-test").create_invocation(tenant_id="tenant-a",
        user_id="user-a", conversation_id="conversation-a", request_id="approve")
    validated = RoutePolicy().accept(proposal, approved, agent._registry)
    compiled = TurnPlanCompiler().compile(validated, approved, agent._registry, identity)
    assert len([item for item in compiled.work.items if item.operation_key]) == count
    assert {w.operation_key for w in writes} == {a.operation_key for a in result.prepared_actions}
    assert len(writes) == count and all(not w.dependencies for w in writes)
    assert all(set(w.command_id for w in writes) <= set(c.dependencies) for c in proposal.commands[count:])
    # Parameter changes must not be authorized by the same set consent.
    from application.work_item import ArgumentValue
    from application.turn_planning import TurnPlanningError
    for index in range(count):
        altered = replace(writes[index], arguments=(ArgumentValue.create("order_id", "OTHER"),))
        tampered = replace(proposal, commands=(*proposal.commands[:index], altered, *proposal.commands[index+1:]))
        with pytest.raises(TurnPlanningError):
            RoutePolicy().accept(tampered, approved, agent._registry)


@pytest.mark.parametrize("count", [1, 2, 4])
def test_confirmation_is_complete_without_author_or_semantic_judge(count):
    _, _, result, state = prepared(count)
    class MustNotCall:
        async def compose(self, *args, **kwargs):
            pytest.fail("scope presentation does not need another author")
        async def verify(self, *args, **kwargs):
            pytest.fail("scope presentation does not need a semantic judge")
    response = asyncio.run(ResponseAssembler(MustNotCall(), knowledge_verifier=MustNotCall(),
        fallback_locale="en").assemble(_board(replace(result, facts=())), current_message="please do all",
                                      pending_approval=state.pending_approval))
    assert response.interaction_ready and not response.verified
    assert response.approval_operation_key == state.pending_approval.scope_key
    for action in result.prepared_actions:
        assert next(a.value for a in action.arguments if a.name == "order_id") in response.text
    assert "prepare_order_cancel" not in response.text and '"order_id":' not in response.text


def test_scope_key_changes_with_any_member_parameters():
    from application.approval_operation import approval_scope_key
    from application.work_item import ArgumentValue
    _, _, _, state = prepared(2)
    operations = state.pending_approval.operations
    for index in range(2):
        changed = replace(operations[index], arguments=(ArgumentValue.create("order_id", "OTHER"),), argument_bindings=())
        assert approval_scope_key((*operations[:index], changed, *operations[index+1:])) != state.pending_approval.scope_key


def test_separate_question_is_not_part_of_the_execution_confirmation():
    from application.agent_result import MissingInputSpec
    _, _, result, state = prepared(2)
    question = MissingInputSpec("reply", "other", "INPUT", "string",
        "Which option? Do you also approve order OTHER?")
    response = asyncio.run(ResponseAssembler(fallback_locale="en").assemble(
        _board(replace(result, facts=())), current_message="continue",
        pending_approval=state.pending_approval, requested_inputs=(question,)))
    assert response.interaction_ready
    before, card = response.text.split("Please confirm these changes", 1)
    assert "not execution approval" in before and question.question_hint in before
    assert "OTHER" not in card
    assert response.approval_operation_key == state.pending_approval.scope_key


@pytest.mark.parametrize("count", [1, 2, 4])
def test_postgres_operation_members_execute_once_across_executor_restart(postgres_database_url, count):
    from uuid import uuid4
    from infrastructure.postgres import PostgresPool, PostgresPoolConfig, PostgresMigrationRunner
    from infrastructure.target_workflow_execution import TargetWorkflowExecutor
    from mcp.tool_manager import ToolEffectReceipt, ToolEffectStatus
    from tests.test_target_framework_agent import _context
    agent, context, result, state = prepared(count)
    pending = state.pending_approval
    writes = []
    async def write(arguments, runtime_context):
        writes.append(arguments["order_id"])
        return ToolEffectReceipt({"order_id": arguments["order_id"], "cancelled": True},
                                 ToolEffectStatus.COMMITTED, "receipt-" + arguments["order_id"])
    tool = next(tool for tool in agent._tool_manager.registered_tools if tool.name == "order_cancel")
    agent._tool_manager.register(replace(tool, handler=write, requires_approval=True,
        receipt_schema_version="action-receipt-v1", schema={**tool.schema,
            "properties": {**tool.schema["properties"], "expected_order_version": {"type": "integer"}}}))
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
    pool.open()
    conversation = "operation-set-" + uuid4().hex
    contexts = [replace(_context(replace(action, approval_binding=pending.approval_id)),
        trusted_context={**context.trusted_context, "conversation_id": conversation,
            "approval_binding": pending.approval_id, "approved_operations": [op.view() for op in pending.operations]})
        for action in result.prepared_actions]
    async def run():
        first = TargetWorkflowExecutor(pool, agent._tool_manager, registry=agent._registry)
        # Stop after the first member; a fresh executor must replay its receipt
        # and execute all untouched members under the SAME consumed scope.
        done = await first(contexts[0])
        assert done.status.value == "SUCCEEDED", done
        fresh = TargetWorkflowExecutor(pool, agent._tool_manager, registry=agent._registry)
        results = await asyncio.gather(*(fresh(ctx) for ctx in contexts))
        assert all(r.status.value == "SUCCEEDED" for r in results), results
        assert len(writes) == len(set(writes)) == count
        assert {r.action_receipts[0].operation_key for r in results} == {op.operation_key for op in pending.operations}
    try:
        asyncio.run(run())
    finally:
        pool.close()


@pytest.fixture(scope="module")
def two_members():
    return prepared(2)


@pytest.mark.parametrize("statuses", itertools.product(
    ["SUCCEEDED", "TERMINAL_FAILURE", "CANCELLED", "BLOCKED", "RECONCILING"], repeat=2))
def test_scope_lifecycle_reduces_all_members_without_losing_success(two_members, statuses):
    from application.agent_result import AgentResult, AgentResultStatus
    from application.result_board import ResultBoardSnapshot
    from application.target_conversation_manager import TargetConversationManager
    from application.deterministic_resolution import DeterministicResolution, ResolutionKind
    _, _, prepared_result, state = two_members
    pending = state.pending_approval
    state = state.consume_approval(approval_id=pending.approval_id,
        approval_version=pending.version, approved=True)
    items = tuple(replace(item, approval_binding=pending.approval_id) for item in prepared_result.prepared_actions)
    results = tuple(AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus(status),
        "TEST", "test") for item, status in zip(items, statuses))
    board = ResultBoardSnapshot(results, (), (), (), (), (), True, True, work_items=items)
    resolution = DeterministicResolution(ResolutionKind.APPROVAL_DECISION, "APPROVED", state.fingerprint,
        workstream_id=pending.workstream_id, signal_id=pending.approval_id,
        signal_version=pending.version, approved=True)
    transitions = []
    updated = TargetConversationManager._apply_successful_workflows(SimpleNamespace(), state,
        SimpleNamespace(work=SimpleNamespace(items=items), transitions=None), board, None, resolution,
        checkpoint_thread_id="checkpoint", transitions=transitions)
    expected = "RECONCILING" if "RECONCILING" in statuses else (
        "COMPLETED" if all(status == "SUCCEEDED" for status in statuses) else "FAILED")
    assert updated.workstreams[-1].status.value == expected
    assert board.results == results  # status projection cannot erase successful members
    assert conversation_state_from_payload(conversation_state_to_payload(updated)) == updated
