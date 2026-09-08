"""Properties of task-ID dependencies and cross-turn outcome projection."""
from dataclasses import replace
from itertools import permutations

import pytest

from application.agent_result import AgentResult, AgentResultStatus
from application.result_board import ResultBoard
from application.work_item import ControlMode, WorkPlan
from tests.test_target_orchestration_runtime import _item, _fact


@pytest.mark.parametrize("status", [AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL,
                                    AgentResultStatus.BLOCKED, AgentResultStatus.RECONCILING])
@pytest.mark.parametrize("covered", [False, True])
def test_dependency_requires_successful_covered_predecessor_in_every_plan_order(status, covered):
    a = _item("a", "general", ControlMode.DIRECT, "a.output")
    b = _item("b", "general", ControlMode.DIRECT, "b.output", dependencies=("a",))
    c = _item("c", "general", ControlMode.DIRECT, "c.output", dependencies=("b",))
    result = AgentResult("a", "general", status, "OBSERVED", "test",
                         facts=(_fact(a, "value"),) if covered else ())
    for order in permutations((a, b, c)):
        board = ResultBoard().evaluate(WorkPlan(order, "a"), (result,))
        if status is AgentResultStatus.SUCCEEDED and covered:
            assert [item.work_item_id for item in board.ready_items] == ["b"]
            assert not board.blocked_results
        else:
            assert not board.ready_items
            assert {item.work_item_id for item in board.blocked_results} == {"b", "c"}
            assert board.complete
        assert not board.task_completed


@pytest.mark.parametrize("retained", [False, True])
def test_another_goal_with_the_same_requirement_cannot_cover_missing_output(retained):
    a = _item("a", "general", ControlMode.DIRECT, "shared.output")
    b = _item("b", "general", ControlMode.DIRECT, "shared.output")
    result_a = AgentResult("a", "general", AgentResultStatus.SUCCEEDED, "DONE", "test")
    result_b = AgentResult("b", "general", AgentResultStatus.SUCCEEDED, "DONE", "test",
                           facts=(_fact(b, "value"),))
    board = ResultBoard().evaluate(
        WorkPlan((b,) if retained else (a, b), "b"),
        (result_b,) if retained else (result_a, result_b),
        retained_outcomes=((a, result_a),) if retained else ())
    assert board.missing_requirement_ids == ("shared.output",)
    assert not board.coverage_complete and not board.task_completed


@pytest.mark.parametrize("retained", [False, True])
def test_retaining_an_outcome_does_not_change_coverage_conflicts_or_partial_delivery(retained):
    a = _item("a", "general", ControlMode.DIRECT, "shared.output")
    b = _item("b", "general", ControlMode.DIRECT, "shared.output")
    fact = _fact(a, "first")
    results = (
        AgentResult("a", "general", AgentResultStatus.SUCCEEDED, "DONE", "test", facts=(fact,)),
        AgentResult("b", "general", AgentResultStatus.PARTIAL, "PARTIAL", "test",
                    facts=(replace(fact, source_ref="receipt:b", value_json='{"value":"second"}'),)))
    board = ResultBoard().evaluate(WorkPlan((b,) if retained else (a, b), "b"),
        results[1:] if retained else results,
        retained_outcomes=((a, results[0]),) if retained else ())
    assert board.conflict_keys == ("subject:a:shared.output",)
    assert not board.coverage_complete and not board.partial_delivery_allowed
    assert not board.task_completed
