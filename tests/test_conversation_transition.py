from dataclasses import replace
from itertools import permutations

import pytest

from application.conversation_state import ConversationState, ConversationStateConflict
from application.conversation_transition import rebase_transition
from tests.test_work_control import _item


def test_disjoint_control_additions_commute_and_replay():
    base = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    plans = [base.accept_work_items((_item(str(i), 1, work_item_id=str(i)),),
                                  invocation_key=str(i)) for i in range(4)]
    fingerprints = set()
    for order in permutations(plans):
        current = base
        for planned in order:
            current = rebase_transition(base, planned, current)
        assert {c.control_id for c in current.work_controls} == {"0", "1", "2", "3"}
        assert current.version == 4
        fingerprints.add(current.fingerprint)
        for planned in order:
            assert rebase_transition(base, planned, current) == current
    assert len(fingerprints) == 1


def test_competing_revisions_do_not_merge():
    base = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    base = base.accept_work_items((_item("a", 1, work_item_id="a1"),), invocation_key="1")
    left = base.accept_work_items((_item("a", 2, work_item_id="a2"),), invocation_key="2")
    right = base.accept_work_items((_item("a", 2, work_item_id="a3"),), invocation_key="3")
    with pytest.raises(ConversationStateConflict):
        rebase_transition(base, left, right)
    with pytest.raises(ConversationStateConflict, match="new work identity"):
        base.accept_work_items((_item("a", 2, work_item_id="a1"),), invocation_key="4")


def test_unchanged_transition_preserves_new_goal_revision():
    base = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    first = base.accept_work_items((_item("a", 1, work_item_id="a1"),), invocation_key="1")
    revised = first.accept_work_items((_item("a", 2, work_item_id="a2"),), invocation_key="2")
    assert rebase_transition(first, first, revised) == revised


def test_revision_invalidates_dependents_but_not_independent_work():
    base = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    first = _item("a", 1, work_item_id="a1")
    dependent = replace(_item("b", 1, work_item_id="b1"), dependencies=("a1",))
    independent = _item("c", 1, work_item_id="c1")
    state = base.accept_work_items((first, dependent, independent), invocation_key="1")
    assert state.accepts_work(dependent)
    state = state.accept_work_items((_item("a", 2, work_item_id="a2"),), invocation_key="2")
    assert not state.accepts_work(dependent)
    assert state.accepts_work(independent)


@pytest.mark.parametrize("depth", [2, 3, 7])
def test_compiled_closure_retires_every_descendant(depth):
    from application.work_item import WorkPlan
    from application.work_control import WorkControlGuard
    from application.conversation_state import InMemoryConversationStateStore
    items = tuple(replace(_item(str(i), 1, work_item_id=str(i)),
        dependencies=(str(i - 1),) if i else ()) for i in range(depth))
    plan = WorkPlan(items, "0")
    base = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    state = base.accept_work_items(items, invocation_key="1")
    store = InMemoryConversationStateStore()
    assert store.compare_and_set(base, state)
    revised = state.accept_work_items((_item("0", 2, work_item_id="new"),), invocation_key="2")
    assert store.compare_and_set(state, revised)
    guard = WorkControlGuard(store)
    for item in items:
        assert not guard.is_current(item, {"tenant_id": "t", "user_id": "u", "conversation_id": "c",
            "dependency_work_ids": plan.dependency_closure(item)})


@pytest.mark.parametrize("depth", [3, 7])
def test_retained_dependency_provenance_survives_plan_changes(depth):
    from types import SimpleNamespace
    from application.work_item import WorkPlan, WorkItemContractError
    from application.agent_result import AgentResult, AgentResultStatus
    from application.result_board import ResultBoard
    from application.work_control import current_result_board
    items = tuple(replace(_item(str(i), 1, work_item_id=str(i)),
        dependencies=(str(i-1),) if i else ()) for i in range(depth))
    unrelated = _item("other", 1, work_item_id="other")
    state = ConversationState.empty(tenant_id="t", user_id="u", conversation_id="c")
    state = state.accept_work_items((*items, unrelated), invocation_key="initial")
    state = state.accept_work_items((_item("0", 2, work_item_id="replacement"),), invocation_key="revision")
    outcomes = tuple((item, AgentResult(item.work_item_id, item.owner_agent,
        AgentResultStatus.SUCCEEDED, "DONE", "test")) for item in items)
    plan = WorkPlan((unrelated,), unrelated.work_item_id)
    board = ResultBoard().evaluate(plan, (), retained_outcomes=outcomes)
    filtered = current_result_board(SimpleNamespace(work=plan), board, state)
    assert all(result.status is AgentResultStatus.SUPERSEDED for _, result in filtered.retained_outcomes)
    assert current_result_board(SimpleNamespace(work=plan), filtered, state) == filtered
    incomplete = replace(board, retained_outcomes=outcomes[1:])
    with pytest.raises(WorkItemContractError, match="incomplete"):
        current_result_board(SimpleNamespace(work=plan), incomplete, state)
