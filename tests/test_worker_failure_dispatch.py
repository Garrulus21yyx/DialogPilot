"""One failure boundary and the existing bounded observation graph."""
import asyncio
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import AgentResult, AgentResultStatus
from application.execution_progress import recovery_observations
from application.orchestration_runtime import OrchestrationRuntime
from application.result_board import ResultBoard
from application.work_item import ControlMode, WorkPlan
from tests.test_target_orchestration_runtime import _item, _fact


@pytest.mark.parametrize("error", [ConnectionError, TimeoutError, RuntimeError])
@pytest.mark.parametrize("reverse", [False, True])
def test_worker_failure_keeps_independent_success_and_blocks_only_dependencies(error, reverse):
    bad = _item("bad", "general", ControlMode.DELEGATED, "fact.bad")
    good = _item("good", "general", ControlMode.DIRECT, "fact.good")
    dependent = _item("dependent", "general", ControlMode.DIRECT, "fact.dep", dependencies=("bad",))
    calls = []
    async def worker(context):
        item = context.work_item
        calls.append(item.work_item_id)
        if item == bad:
            raise error("dependency unavailable")
        return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
            "DONE", "test", facts=(_fact(item, "done"),))
    plan = WorkPlan((dependent, good, bad) if reverse else (bad, good, dependent), bad.work_item_id)
    board = asyncio.run(OrchestrationRuntime(direct_executor=worker,
        domain_workers={"general": worker}).execute(plan, current_message="do both"))
    results = {r.work_item_id: r for r in board.results}
    assert set(calls) == {"good", "bad"} and len(calls) == 2
    assert results["good"].status is AgentResultStatus.SUCCEEDED
    assert results["dependent"].status is AgentResultStatus.BLOCKED
    assert results["bad"].retryable is (error is not RuntimeError)
    assert results["bad"].execution_feedback[0]["detail"]["error_type"] == error.__name__
    assert bool(recovery_observations(board)) is (error is not RuntimeError)
    assert board.facts and not board.task_completed


def test_cancellation_is_not_converted_to_retryable_failure():
    from langgraph.errors import NodeCancelledError
    item = _item("cancel", "general", ControlMode.DELEGATED, "fact.a")
    async def worker(_context):
        raise asyncio.CancelledError()
    with pytest.raises(NodeCancelledError) as raised:
        asyncio.run(OrchestrationRuntime(direct_executor=worker, domain_workers={"general": worker}).execute(
            WorkPlan((item,), item.work_item_id), current_message="stop"))
    assert isinstance(raised.value.__cause__, asyncio.CancelledError)


def test_escaped_write_failure_is_left_to_ledger_recovery_not_worker_retry():
    from tests.test_write_workflow import _item as write_item
    item, calls = write_item(), []
    async def worker(context):
        calls.append(context.work_item.operation_key)
        raise ConnectionError("ledger unavailable; effect must be reconciled")
    with pytest.raises(ConnectionError):
        asyncio.run(OrchestrationRuntime(direct_executor=worker, domain_workers={},
            workflow_executor=worker).execute(WorkPlan((item,), item.work_item_id), current_message="execute"))
    assert calls == [item.operation_key]


def test_direct_worker_deadline_returns_typed_failure():
    item = replace(_item("slow", "general", ControlMode.DIRECT, "fact.a"), timeout_seconds=1)
    async def worker(_context):
        await asyncio.Event().wait()
    board = asyncio.run(OrchestrationRuntime(direct_executor=worker, domain_workers={}).execute(
        WorkPlan((item,), item.work_item_id), current_message="lookup"))
    assert board.results[0].reason_code == "WORKER_EXECUTION_FAILED:TimeoutError"
    assert board.results[0].retryable


@pytest.mark.parametrize("status", [AgentResultStatus.SUCCEEDED, AgentResultStatus.BLOCKED,
    AgentResultStatus.RETRYABLE_FAILURE, AgentResultStatus.TERMINAL_FAILURE,
    AgentResultStatus.RECONCILING, AgentResultStatus.SUPERSEDED, AgentResultStatus.WAITING_APPROVAL])
@pytest.mark.parametrize("write", [False, True])
def test_only_actionable_current_read_failures_trigger_replanning(status, write):
    from tests.test_write_workflow import _item as write_item
    item = write_item() if write else _item("read", "general", ControlMode.DELEGATED, "fact.read")
    result = AgentResult(item.work_item_id, item.owner_agent, status, "TEST", "test",
                         retryable=status is AgentResultStatus.RETRYABLE_FAILURE)
    plan = WorkPlan((item,), item.work_item_id)
    board = ResultBoard().evaluate(plan, (result,))
    assert bool(recovery_observations(board)) is (not write and status is AgentResultStatus.RETRYABLE_FAILURE)
    other = _item("other", "general", ControlMode.DIRECT, "fact.other")
    retained = ResultBoard().evaluate(WorkPlan((other,), other.work_item_id), (), retained_outcomes=((item, result),))
    assert not recovery_observations(retained)


@pytest.mark.parametrize("ending", ["recover", "planner_failure", "repeat", "report"])
@pytest.mark.parametrize("failure_kind", ["connection", "no_progress"])
@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_failure_replanning_preserves_results_has_budget_and_replays(ending, failure_kind, backend, request):
    from application.conversation_state import InMemoryConversationStateStore
    from application.default_capability_registry import build_default_capability_registry
    from application.deterministic_resolution import TurnObservations
    from application.response_assembly import ResponseAssembler
    from application.target_conversation_manager import TargetConversationManager
    from application.turn_planning import CommandKind, TurnProposal, ProposalDisposition, PlanningUnavailable
    from application.turn_runtime import TurnRuntime
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    from tests.test_turn_runtime import _OrderUnderstanding, _Executor
    from tests.test_knowledge_answer_boundary import Verifier

    async def run(saver):
        observed, calls = [], []
        class Understanding:
            async def __call__(self, message, state, deterministic, registry, context):
                base = (await _OrderUnderstanding()()).commands[0]
                if context.observed_execution is None:
                    return TurnProposal(ProposalDisposition.RESOLVED, (
                        replace(base, command_id="good", objective="Read shipment"),
                        replace(base, command_id="bad", kind=CommandKind.DELEGATE_TASK,
                            tool_id=None, objective="Investigate delivery problem")), "TEST")
                observed.append(context.observed_execution)
                assert any(r.facts for r in context.observed_execution.all_results)
                if ending == "planner_failure":
                    raise PlanningUnavailable(ProposalDisposition.PROVIDER_FAILURE, "TEST_PLANNER_DOWN")
                if ending == "report":
                    return TurnProposal(ProposalDisposition.RESPOND, (), "REPORT_FAILURE",
                        response_text="The shipment was checked, but the investigation service is unavailable.")
                original = recovery_observations(context.observed_execution)[0][0]
                return TurnProposal(ProposalDisposition.RESOLVED, (replace(base,
                    kind=CommandKind.DELEGATE_TASK, tool_id=None,
                    objective=original.objective, revises_control_id=original.control.control_id),), "REPLAN")
        executor = _Executor()
        async def worker(context):
            calls.append(context.work_item.control_mode)
            if context.work_item.control_mode is ControlMode.DELEGATED and (
                    ending != "recover" or calls.count(ControlMode.DELEGATED) == 1):
                if failure_kind == "no_progress":
                    return AgentResult(context.work_item.work_item_id, context.work_item.owner_agent,
                        AgentResultStatus.BLOCKED, "AGENT_NO_PROGRESS", "test")
                raise ConnectionError("service unavailable")
            return await executor(context)
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
            orchestration=OrchestrationRuntime(direct_executor=worker,
                domain_workers={"order_logistics": worker}, checkpointer=saver))
        runtime = TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)),
                              checkpointer=saver, max_observation_steps=2)
        from uuid import uuid4
        from core.identity import IdentityFactory
        identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
            conversation_id="failure-" + uuid4().hex, request_id="request")
        message = TurnObservations("Check shipment and investigate the delivery problem")
        result = await runtime.execute(identity, message)
        assert observed and calls.count(ControlMode.DIRECT) == 1
        assert result.managed.board.facts
        assert result.assembled is not None
        if ending == "recover":
            assert calls.count(ControlMode.DELEGATED) == 2
            assert result.managed.board.task_completed
        elif ending != "report":
            assert not result.managed.board.task_completed
            assert result.managed.diagnostics
            assert calls.count(ControlMode.DELEGATED) <= 3
        else:
            assert not result.managed.board.task_completed
            assert len(observed) == 1 and calls.count(ControlMode.DELEGATED) == 1
        count = len(calls)
        await TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)),
            checkpointer=saver, max_observation_steps=2).execute(identity, message)
        assert len(calls) == count
    async def scenario():
        if backend == "memory":
            await run(InMemorySaver(serde=target_checkpoint_serializer()))
        else:
            from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
            url = request.getfixturevalue("postgres_database_url")
            async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                await run(saver)
    asyncio.run(scenario())
