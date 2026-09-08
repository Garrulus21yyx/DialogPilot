"""Known fact conflicts propagate along hard edges, never unrelated branches."""
from dataclasses import replace
from itertools import permutations

import pytest

from application.agent_result import AgentResult, AgentResultStatus
from application.result_board import ResultBoard
from application.response_assembly import _response_context, _allowed_claims, _render_board
from application.work_item import ControlMode, WorkPlan
from tests.test_target_orchestration_runtime import _item, _fact


def scenario():
    a = _item('policy', 'general', ControlMode.DIRECT, 'policy.term')
    b = _item('other-source', 'general', ControlMode.DIRECT, 'policy.term')
    c = _item('eligibility', 'general', ControlMode.DIRECT, 'eligibility', dependencies=('policy',))
    d = _item('warranty', 'general', ControlMode.DIRECT, 'warranty')
    e = _item('action-check', 'general', ControlMode.DIRECT, 'action-check', dependencies=('eligibility',))
    original = _fact(a, 'seven')
    facts = (original, replace(original, source_ref='another-source', value_json='{"value":"thirty"}'),
             _fact(c, 'eligible'), _fact(d, 'one-year'))
    results = tuple(AgentResult(w.work_item_id, w.owner_agent, AgentResultStatus.SUCCEEDED,
        'DONE', 'test', facts=(fact,)) for w, fact in zip((a,b,c,d), facts))
    return (a,b,c,d,e), results


def test_late_conflict_invalidates_completed_downstream_and_blocks_next_step_in_every_order():
    items, results = scenario()
    for order in permutations(items):
        plan = WorkPlan(order, 'policy')
        # Already completed eligibility remains an execution fact. Its conclusion
        # loses support when a later independent source contradicts its premise.
        before = ResultBoard().evaluate(plan, (results[0], results[2], results[3]))
        assert before.coverage_for(items[2], results[2])['deliverable']
        for observed in (results, tuple(reversed(results))):
            board = ResultBoard().evaluate(plan, observed)
            assert board.partial_delivery_allowed
            assert not board.task_completed
            for item, result in board.outcome_items:
                row = board.coverage_for(item, result)
                assert bool(row['conflict_keys']) == (item.work_item_id != 'warranty')
                assert row['deliverable'] == (item.work_item_id == 'warranty')
            assert board.results[order.index(items[2])].status is AgentResultStatus.SUCCEEDED
            assert {r.work_item_id for r in board.blocked_results} == {'action-check'}
            assert not board.ready_items


@pytest.mark.parametrize('retained', [False, True])
def test_shared_conflict_without_an_independent_supported_result_has_no_partial_delivery(retained):
    items, results = scenario()
    selected = items[:3]
    board = ResultBoard().evaluate(WorkPlan((items[1],) if retained else selected, 'other-source'),
        (results[1],) if retained else results[:3],
        retained_outcomes=((items[0],results[0]),(items[2],results[2])) if retained else ())
    assert not board.partial_delivery_allowed
    assert not board.coverage_for(items[1],results[1])['deliverable']


def test_retained_local_id_cannot_make_unrelated_current_dependency_conflicted():
    items, results = scenario()
    current_parent = replace(items[3], work_item_id='policy')
    current_result = replace(results[3], work_item_id='policy')
    current_child = replace(items[2], requirement_ids=('new.output',))
    child_result = replace(results[2], facts=(_fact(current_child, 'new-safe'),))
    board = ResultBoard().evaluate(WorkPlan((current_parent,current_child), 'policy'),
        (current_result,child_result), retained_outcomes=tuple(zip(items[:2],results[:2])))
    assert board.partial_delivery_allowed
    assert board.coverage_for(current_child,child_result)['deliverable']
    assert not board.coverage_for(items[0],results[0])['deliverable']


@pytest.mark.parametrize('knowledge_safe', [False, True])
def test_authoring_projection_and_fallback_retain_limits_and_independent_answer(monkeypatch, knowledge_safe):
    items, results = scenario()
    board = ResultBoard().evaluate(WorkPlan(items,'policy'),results)
    context = _response_context(board)
    assert len(context['facts']) == 4  # Original conflicting facts remain inspectable.
    assert {r['work_item_id'] for r in context['outcomes'] if r['coverage']['deliverable']} == {'warranty'}
    assert {c.claim_id for c in _allowed_claims(board) if c.kind == 'FACT'} == {'fact:warranty:1'}
    monkeypatch.setattr('application.response_assembly._render_verified_facts',
        lambda result, locale: '\n'.join(f.value_json for f in result.facts))
    text = _render_board(board,locale='en', knowledge_safe=knowledge_safe)
    assert 'one-year' in text and 'Conflicting evidence' in text
    assert 'eligible' not in text and 'seven' not in text and 'thirty' not in text
