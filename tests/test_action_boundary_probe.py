import json
from pathlib import Path

import pytest

from scripts.evaluate_action_boundary import fixture


CASES = json.loads((Path(__file__).parents[1] /
    'data/eval/action-boundary-fresh6-2026-09-09.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_fresh_probe_uses_production_wrappers_and_explicit_target(case):
    worker, context, tools, policy = fixture(case, None, None)
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
