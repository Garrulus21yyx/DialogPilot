import asyncio
from dataclasses import replace

import pytest

from application.agent_result import AgentResult, AgentResultStatus
from application.orchestration_runtime import (
    AgentContextView,
    OrchestrationRuntime,
    OrchestrationRuntimeError,
    _checkpoint_matches_work_plan,
    _legacy_work_plan_fingerprint,
    _merge_agent_results,
)
from application.work_item import (
    ActionSerialization,
    ControlMode,
    WorkControlBinding,
    WorkPlan,
    WorkPlanPolicy,
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


def test_agent_result_reducer_accepts_idempotent_replay_and_rejects_conflict():
    result = AgentResult("work-a", "general", AgentResultStatus.SUCCEEDED, "DONE", "test")
    replayed = _merge_agent_results([result], [result])

    assert replayed == [result]
    with pytest.raises(OrchestrationRuntimeError, match="conflicting results"):
        _merge_agent_results(
            [result],
            [replace(result, status=AgentResultStatus.TERMINAL_FAILURE)],
        )


def test_legacy_plan_fingerprint_is_accepted_only_for_equivalent_policy():
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

    assert _checkpoint_matches_work_plan(_legacy_work_plan_fingerprint(read_plan), read_plan)
    assert not _checkpoint_matches_work_plan(
        _legacy_work_plan_fingerprint(unsafe_action_plan),
        unsafe_action_plan,
    )


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
