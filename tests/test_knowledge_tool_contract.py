import asyncio
from copy import deepcopy
from dataclasses import replace
import pytest
from application.agent_result import AgentResultStatus
from application.knowledge_tool_contract import knowledge_outcome
from application.work_item import ControlMode
from infrastructure.target_tool_execution import TargetToolExecutor
from infrastructure.target_framework_agent import _adapt_framework_result
from mcp.tool_manager import ToolResult
from tests.test_target_framework_agent import _context, _item


def evidence_result(text='仅未拆封商品可以退货。'):
    return {'status': 'OK', 'evidence_pack': {
        'query': '退货条件', 'index_manifest_fingerprint': 'a'*64,
        'items': [{'chunk_id': 'child-1', 'title': '退货政策', 'text': text,
                   'source_ref': {'source_id': 'policy', 'source_revision': 'v1',
                                  'start_char': 0, 'end_char': len(text),
                                  'scope': 'public', 'source_type': 'text', 'checksum': 'b'*64}}]}}


@pytest.mark.parametrize('status,expected', [
    ('NO_EVIDENCE', AgentResultStatus.BLOCKED),
    ('AMBIGUOUS', AgentResultStatus.BLOCKED),
    ('UNAVAILABLE', AgentResultStatus.RETRYABLE_FAILURE),
    ('INVALID_CONTRACT', AgentResultStatus.TERMINAL_FAILURE),
    ('CONFLICT', AgentResultStatus.TERMINAL_FAILURE),
    ('UNKNOWN', AgentResultStatus.TERMINAL_FAILURE),
])
def test_domain_outcomes_preserved_in_both_execution_modes(status, expected):
    data = {'status': status, 'evidence_pack': None}
    result = ToolResult(True, data, 'knowledge_search', authority='knowledge.active_source', call_id='read')
    class Tools:
        async def execute_for_agent(self, *args, **kwargs): return result
    item = replace(_item(allowed_tools=('knowledge_search',)), control_mode=ControlMode.DIRECT,
                   requirement_ids=('knowledge.active_source',))
    direct = asyncio.run(TargetToolExecutor(Tools())(_context(item)))
    delegated = _adapt_framework_result(_context(item), (result,), (), 'test')
    for output in (direct, delegated):
        assert output.status is expected
        assert not output.facts
        assert output.retryable == (expected is AgentResultStatus.RETRYABLE_FAILURE)


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(status='NO_EVIDENCE'),
    lambda d: d['evidence_pack'].update(items=[]),
    lambda d: d['evidence_pack']['items'].append(deepcopy(d['evidence_pack']['items'][0])),
    lambda d: d['evidence_pack']['items'][0]['source_ref'].update(end_char=999),
    lambda d: d['evidence_pack']['items'][0]['source_ref'].update(scope='private'),
])
def test_malformed_evidence_never_satisfies_requirement(mutate):
    data = evidence_result()
    mutate(data)
    assert knowledge_outcome(data)[0] is AgentResultStatus.TERMINAL_FAILURE


@pytest.mark.parametrize('data', [None, {}, {'status': []}, {'status': 1},
                                  {'status': 'OK', 'evidence_pack': []}])
def test_unknown_wire_values_fail_in_typed_way(data):
    assert knowledge_outcome(data)[0] is AgentResultStatus.TERMINAL_FAILURE
