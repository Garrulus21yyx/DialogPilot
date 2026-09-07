"""Sequential reads, concurrent conflicts and cached observations have distinct meanings."""
import asyncio
import itertools
import json
import pytest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from application.agent_result import FactRecord, FactSourceKind
from application.result_board import current_facts, ResultBoard
from application.response_assembly import _allowed_claims, _render_board
from infrastructure.target_agent_result_adapter import fact_from_tool_result, framework_artifact, restore_framework_artifact
from tests.test_target_framework_agent import _item
from tests.test_response_assembly import _board, _result


def fact(number, start, end, **changes):
    origin = datetime(2026, 9, 7, tzinfo=timezone.utc)
    return replace(FactRecord('query:record', 'environment.read_record', json.dumps({'state': number}, separators=(',', ':')),
        FactSourceKind.VERIFIED_STATE, f'call:{number}', 'read_record', 'v1',
        origin + timedelta(seconds=end), observation_started_at=origin + timedelta(seconds=start)), **changes)


def test_sequential_projection_is_order_independent_and_idempotent():
    observations = (fact(1, 0, 1), fact(2, 2, 3), fact(3, 4, 5))
    for order in itertools.permutations(observations):
        assert current_facts(order) == (observations[-1],)
        assert current_facts(current_facts(order)) == current_facts(order)
        result = replace(_result('w', 'general'), facts=order)
        claims = _allowed_claims(_board(result))
        assert [c.value for c in claims if c.kind == 'FACT'] == [{'state': 3}]
        assert result.facts == order  # original evidence remains inspectable


def test_overlap_unknown_order_and_distinct_authorities_do_not_supersede():
    old = fact(1, 0, 3)
    candidates = [fact(2, 2, 4), fact(2, 4, 5, observation_started_at=None),
                  fact(2, 4, 5, producer_id='another_source'),
                  fact(2, 4, 5, source_kind=FactSourceKind.USER_ASSERTED)]
    for new in candidates:
        assert current_facts((old, new)) == (old, new)
        assert ResultBoard._conflicts(current_facts((old, new)))


def test_independent_queries_never_overwrite_each_other():
    first, second = fact(1, 0, 1), fact(2, 2, 3, subject_ref='query:other-record')
    assert current_facts((first, second)) == (first, second)
    assert not ResultBoard._conflicts((first, second))


def test_later_read_cannot_erase_a_corrupted_evidence_identity():
    first = fact(1, 0, 1)
    conflicting = replace(first, value_json='{"state":99}')
    latest = fact(3, 4, 5)
    assert ResultBoard._conflicts(current_facts((first, conflicting, latest)))


def test_same_source_cannot_acquire_new_temporal_authority_on_merge():
    from application.agent_result import merge_facts, AgentResultContractError
    first = fact(1, 0, 1)
    altered = fact(1, 4, 5)
    middle = fact(2, 2, 3)
    for order in itertools.permutations((first, altered, middle)):
        with pytest.raises(AgentResultContractError, match='observation metadata'):
            merge_facts(order)
        assert ResultBoard._conflicts(current_facts(order))


def test_resumed_worker_receives_current_view_without_erasing_saved_history():
    from application.orchestration_runtime import OrchestrationRuntime
    old, new = fact(1, 0, 1), fact(2, 2, 3)
    item = _item()
    state = {'ready_items': (item,), 'facts': (new,),
             'continuation_facts': {item.work_item_id: (old,)}}
    runtime = object.__new__(OrchestrationRuntime)
    sends = runtime._dispatch(state)
    assert sends[0].arg['facts'] == (new,)
    assert state['continuation_facts'][item.work_item_id] == (old,)


def test_tool_cache_and_artifact_preserve_original_observation_interval():
    from mcp.tool_manager import MCPToolManager, Tool
    manager = MCPToolManager('test-key', model='test')
    calls = []
    async def read(params, context):
        calls.append(params)
        return {'status': 'ready'}
    manager.register(Tool('read_record', 'Read a record', read,
        {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']},
        allowed_agents=('general',), authority='record.state', cache_ttl=60))
    async def run():
        args = dict(agent_type='general', context={'tenant_id': 't', 'user_id': 'u'})
        first = await manager.execute_for_agent('read_record', {'id': '1'}, call_id='a', **args)
        cached = await manager.execute_for_agent('read_record', {'id': '1'}, call_id='b', **args)
        second = await manager.execute_for_agent('read_record', {'id': '2'}, call_id='c', **args)
        return first, cached, second
    first, cached, second = asyncio.run(run())
    assert len(calls) == 2 and cached.cached
    assert first.observation_started_at <= first.observed_at
    assert (cached.observation_started_at, cached.observed_at) == (first.observation_started_at, first.observed_at)
    restored = restore_framework_artifact(framework_artifact(first))
    original_fact = fact_from_tool_result(_item(), first)
    assert fact_from_tool_result(_item(), restored) == original_fact
    assert fact_from_tool_result(_item(), second).subject_ref != original_fact.subject_ref


def test_committed_action_and_unrenderable_read_do_not_claim_execution_failure():
    from application.agent_result import ReceiptRef
    committed = replace(_result('write', 'retail'), action_receipts=(
        ReceiptRef('r', 'v1', 'op', 'COMMITTED', 'environment.update'),))
    read = replace(_result('read', 'retail'), facts=(fact(2, 2, 3),))
    text = _render_board(_board(committed, read), locale='en')
    assert 'request has been submitted' in text
    assert 'detailed reply could not be verified' in text
    assert 'Please try again' not in text and 'cannot provide a reliable answer' not in text
