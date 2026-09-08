import json
from pathlib import Path

import pytest

from scripts.evaluate_action_boundary import fixture


CASES = json.loads((Path(__file__).parents[1] /
    'data/eval/action-boundary-fresh6-2026-09-09.json').read_text())
TRANSITION_CASES = json.loads((Path(__file__).parents[1] /
    'data/eval/action-boundary-transition-holdout6-2026-09-09.json').read_text())
FEASIBLE_CASES = json.loads((Path(__file__).parents[1] /
    'data/eval/action-boundary-feasible2-2026-09-09.json').read_text())


@pytest.mark.parametrize('case', CASES + TRANSITION_CASES + FEASIBLE_CASES, ids=lambda c: c['id'])
def test_fresh_probe_uses_production_wrappers_and_explicit_target(case):
    worker, context, tools, policy, external_calls = fixture(case, None, None)
    by_name = {tool.name: tool for tool in tools}
    assert 'prepare_' + case['candidate'] in by_name
    assert case['arguments']['object_id'] in case['facts']
    for name, description in case['tools'].items():
        tool = by_name['prepare_' + name]
        assert tool.description.endswith(description)
        assert description in policy
        assert name not in by_name
    assert {'request_user_input', 'report_blocked'} <= by_name.keys()
    assert context.verified_facts[0].source_ref == 'fixture:' + case['id']
    assert worker._build_prompt(context)
    assert external_calls == []


def test_full_worker_fixture_can_prepare_without_invoking_a_business_handler():
    import asyncio
    from langchain_core.messages import AIMessage
    from tests.test_target_framework_agent import ScriptedToolModel
    case = next(case for case in CASES if case['id'] == 'close_and_adjust')
    model = ScriptedToolModel(responses=[AIMessage(content='', tool_calls=[{
        'name': 'prepare_adjust', 'args': {'object_id': 'C7', 'destination': 'Paris'}, 'id': 'proposal'}]),
        AIMessage(content='Address adjustment is prepared; closing remains for after it executes.')])
    worker, context, _, _, external_calls = fixture(case, model, model)
    result = asyncio.run(worker(context))
    assert result.status.value == 'WAITING_APPROVAL'
    assert result.pending_action.action_ref == 'adjust:v1'
    assert result.action_receipts == ()
    assert external_calls == []
