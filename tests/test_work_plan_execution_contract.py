import asyncio
import itertools
from dataclasses import replace

import pytest

from application.agent_result import AgentResult, AgentResultStatus
from application.orchestration_runtime import (
    AgentContextView,
    OrchestrationRuntime,
    OrchestrationRuntimeError,
    _checkpoint_matches_work_plan,
    _validate_checkpoint,
    _merge_agent_results,
    _scope_result,
)
from application.work_item import (
    ActionSerialization,
    ControlMode,
    WorkControlBinding,
    WorkPlan,
    WorkPlanPolicy,
    WorkItemContractError,
)
from tests.test_target_orchestration_runtime import _fact, _item


def test_work_plan_policy_is_part_of_plan_identity_and_retention_contract():
    original = replace(
        _item("work-a", "general", ControlMode.DIRECT, "fact.a"),
        control=WorkControlBinding("goal-a", 1),
    )
    continued = replace(
        original,
        work_item_id="work-a-v2",
        control=WorkControlBinding("goal-a", 2),
        continuation_of=original.work_item_id,
    )
    default = WorkPlan((continued,), continued.work_item_id)
    no_action_serialization = WorkPlan(
        (continued,),
        continued.work_item_id,
        WorkPlanPolicy(action_serialization=ActionSerialization.NONE),
    )

    assert default.fingerprint != no_action_serialization.fingerprint
    assert default.unreplaced_outcomes(((original, None),)) == ()
    with pytest.raises(WorkItemContractError, match="policy"):
        WorkPlan((continued,), continued.work_item_id, {})


def test_agent_result_reducer_accepts_idempotent_replay_and_rejects_conflict():
    result = AgentResult("work-a", "general", AgentResultStatus.SUCCEEDED, "DONE", "test")
    item = _item("work-a", "general", ControlMode.DIRECT, "fact.a")
    record = _scope_result(WorkPlan((item,), item.work_item_id), result)
    replayed = _merge_agent_results([record], [record])

    assert replayed == [record]
    with pytest.raises(OrchestrationRuntimeError, match="conflicting results"):
        _merge_agent_results(
            [record],
            [replace(record, result=replace(result, status=AgentResultStatus.TERMINAL_FAILURE))],
        )


def test_agent_result_reducer_identity_includes_plan_scope():
    first = _item("local", "general", ControlMode.DIRECT, "fact.first")
    second = replace(first, requirement_ids=("fact.second",))
    result = AgentResult("local", "general", AgentResultStatus.SUCCEEDED, "DONE", "test")
    first_record = _scope_result(WorkPlan((first,), first.work_item_id), result)
    second_record = _scope_result(WorkPlan((second,), second.work_item_id), result)

    assert len(_merge_agent_results([first_record], [second_record])) == 2
    with pytest.raises(OrchestrationRuntimeError, match="conflicting results"):
        _merge_agent_results(
            [first_record],
            [replace(first_record, result=replace(result, reason_code="CHANGED"))],
        )


def test_only_current_plan_fingerprint_is_accepted():
    read = _item("read", "general", ControlMode.DIRECT, "fact.read")
    action_capable = replace(
        _item("action", "general", ControlMode.DELEGATED, "fact.action"),
        allowed_actions=("general.write:v1",),
    )
    read_plan = WorkPlan(
        (read,),
        read.work_item_id,
        WorkPlanPolicy(action_serialization=ActionSerialization.NONE),
    )
    unsafe_action_plan = WorkPlan(
        (action_capable,),
        action_capable.work_item_id,
        WorkPlanPolicy(action_serialization=ActionSerialization.NONE),
    )

    assert _checkpoint_matches_work_plan(read_plan.fingerprint, read_plan)
    assert not _checkpoint_matches_work_plan(
        read_plan.fingerprint,
        unsafe_action_plan,
    )


@pytest.mark.parametrize("foreign", [False, True])
def test_graph_consumers_reject_unscoped_or_foreign_results(foreign):
    item = _item("local", "general", ControlMode.DIRECT, "fact.a")
    plan = WorkPlan((item,), item.work_item_id)
    result = AgentResult("local", "general", AgentResultStatus.SUCCEEDED, "DONE", "test",
                         facts=(_fact(item, "old"),))
    other = WorkPlan((replace(item, objective="different goal"),), item.work_item_id)
    record = _scope_result(other, result) if foreign else result
    state = {"work_plan": plan, "work_plan_fingerprint": plan.fingerprint,
             "agent_results": [record]}
    runtime = OrchestrationRuntime(direct_executor=None, domain_workers={})
    for consumer in (_validate_checkpoint, runtime._evaluate, runtime._ready_wave):
        with pytest.raises(OrchestrationRuntimeError):
            consumer(state)
    if not foreign:
        with pytest.raises(OrchestrationRuntimeError):
            _merge_agent_results([], [record])


def test_scoped_reducer_is_associative_and_replay_idempotent_across_waves():
    items = tuple(_item(str(i), "general", ControlMode.DIRECT, f"fact.{i}") for i in range(3))
    plan = WorkPlan(items, items[0].work_item_id)
    records = tuple(_scope_result(plan, AgentResult(item.work_item_id, item.owner_agent,
        AgentResultStatus.SUCCEEDED, "DONE", "test")) for item in items)
    for a, b, c in itertools.permutations(records):
        left = _merge_agent_results(_merge_agent_results([a], [b]), [c])
        right = _merge_agent_results([a], _merge_agent_results([b], [c]))
        assert left == right
        assert _merge_agent_results(left, [c, a, b]) == left


@pytest.mark.parametrize("entry", ["execute", "resume", "cancel_interrupt"])
def test_completed_checkpoint_cannot_bypass_scope_validation(entry):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from application.result_board import ResultBoard
    item = _item("local", "general", ControlMode.DIRECT, "fact.a")
    plan = WorkPlan((item,), item.work_item_id)
    result = AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
        "DONE", "test", facts=(_fact(item, "old"),))
    board = ResultBoard().evaluate(plan, (result,))
    assert board.complete
    # A legacy raw record is not legitimized by a cached complete projection.
    snapshot = SimpleNamespace(values={"work_plan": plan, "work_plan_fingerprint": plan.fingerprint,
        "agent_results": [result], "board": board}, tasks=())
    runtime = OrchestrationRuntime(direct_executor=None, domain_workers={})
    runtime._checkpointer = object()
    runtime.graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot), ainvoke=AsyncMock())
    async def run():
        if entry == "cancel_interrupt":
            return await runtime.cancel_interrupt(thread_id="thread")
        return await getattr(runtime, entry)(plan, current_message="continue", thread_id="thread")
    with pytest.raises(OrchestrationRuntimeError, match="plan-scoped"):
        asyncio.run(run())
    runtime.graph.ainvoke.assert_not_called()


def test_checkpoint_policy_is_explicit_and_current_shape_roundtrips():
    import ormsgpack
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer, TargetCheckpointContractError
    item = _item("local", "general", ControlMode.DIRECT, "fact.a")
    plan = WorkPlan((item,), item.work_item_id)
    serde = target_checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(plan)) == plan
    # SDK kwargs constructor encoding without the new policy must not invoke
    # WorkPlan's fresh-construction default when reading an old checkpoint.
    kind, payload = serde.dumps_typed(plan)
    def remove_policy(code, raw):
        fields = ormsgpack.unpackb(raw, ext_hook=lambda c, b: ormsgpack.Ext(c, b))
        if isinstance(fields, (tuple, list)) and tuple(fields[:2]) == ("application.work_item", "WorkPlan"):
            fields[2].pop("policy")
        return ormsgpack.Ext(code, ormsgpack.packb(fields))
    old = ormsgpack.packb(ormsgpack.unpackb(payload, ext_hook=remove_policy))
    with pytest.raises(TargetCheckpointContractError, match="explicit WorkPlan policy"):
        serde.loads_typed((kind, old))


def test_runtime_action_serialization_is_driven_by_work_plan_policy():
    first = replace(
        _item("first", "general", ControlMode.DELEGATED, "fact.first"),
        allowed_actions=("general.write:v1",),
    )
    second = replace(
        _item("second", "general", ControlMode.DELEGATED, "fact.second"),
        allowed_actions=("general.write:v1",),
    )
    calls = []

    async def worker(context: AgentContextView):
        calls.append((context.work_item.work_item_id, "start"))
        await asyncio.sleep(0.01)
        calls.append((context.work_item.work_item_id, "end"))
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "DONE",
            "test",
            facts=(_fact(context.work_item, "done"),),
        )

    runtime = OrchestrationRuntime(
        direct_executor=worker,
        domain_workers={"general": worker},
    )
    board = asyncio.run(runtime.execute(
        WorkPlan(
            (first, second),
            first.work_item_id,
            WorkPlanPolicy(action_serialization=ActionSerialization.NONE),
        ),
        current_message="run both",
    ))

    assert board.task_completed
    assert [event for _, event in calls[:2]] == ["start", "start"]
