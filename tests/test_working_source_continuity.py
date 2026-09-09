import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, RemoveMessage, messages_to_dict
from langgraph.store.memory import InMemoryStore

from infrastructure.target_context_compaction import ContextCompaction, ToolResultPersistence
from infrastructure.target_model_context import delegated_task_content, delegated_working_input
from infrastructure.target_result_archive import TargetResultArchive, ResultArchiveError
from infrastructure.target_working_sources import SOURCE_INDEX_ID, working_source_index
from tests.test_target_framework_agent import _context, ScriptedToolModel


def task(facts=(), observations=()):
    return delegated_task_content(dict(objective='Inspect then prepare', arguments={}, requirements=[],
        action_proposals_allowed=True, source_conversation={'current_message': 'Use my gift card'},
        recent_relevant_turns=[], verified_facts=list(facts), business_observations=list(observations)))


@pytest.mark.parametrize('count', [1, 4, 9])
def test_summary_without_references_and_continuation_keep_original_source_navigation(count):
    async def run():
        context = _context()
        archive = TargetResultArchive(InMemoryStore())
        persistence = ToolResultPersistence(archive)
        fact = {'subject_ref': 'order:1', 'source_ref': 'parent-read', 'observed_at': '2026-09-09T10:00:00Z',
                'valid_until': None, 'value': {'status': 'pending', 'items': list(range(count))}}
        messages, pinned = delegated_working_input([], task([fact]), 'initial')
        messages.insert(0, HumanMessage(content='Old dialogue. ' * 1300))
        originals = {}
        for i in range(count):
            call = {'id': f'lookup-{i}', 'name': 'catalog_lookup', 'args': {'id': str(i)}}
            data = {'id': str(i), 'variants': [{'price': 19.25, 'available': True}]}
            raw = ToolMessage(content=json.dumps(data), tool_call_id=call['id'], artifact={
                'schema': 'tool-result-v1', 'result': {'success': True, 'data': data,
                    'observed_at': '2026-09-09T10:00:01Z'}})
            async def handler(_request):
                return raw
            update = await persistence.awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=context)), handler)
            messages.extend([AIMessage(content='', tool_calls=[call]), update.update['messages'][0]])
            originals[update.update['messages'][0].artifact['reference']] = raw.content
        before = messages_to_dict(messages)
        model = ScriptedToolModel(responses=[AIMessage(content='Checks completed. No source references.'),
                                            AIMessage(content='Older checks completed.')])
        compact = ContextCompaction(model, archive, available_tokens=6000, overhead_tokens=100,
                                    pinned_message=pinned, soft_fraction=.5, summary_fraction=.65)
        update = await compact.abefore_model({'messages': messages}, SimpleNamespace(context=context))
        assert update['compaction_records'][0]['summary_applied']
        assert messages_to_dict(messages) == before
        kept = [m for m in update['messages'] if not isinstance(m, RemoveMessage)]
        directory = next(m for m in kept if m.id == SOURCE_INDEX_ID)
        entries = json.loads(directory.content)['working_sources']
        assert len(entries) == count + 1
        for entry in entries:
            ref = entry['read_tool_result']['reference']
            page = await archive.read(context, ref, limit=4000)
            if ref in originals:
                assert page['text'] == originals[ref]
                assert entry['arguments'] == {'id': json.loads(page['text'])['id']}
                assert entry['observation']['observed_at'] == '2026-09-09T10:00:01Z'
            else:
                assert json.loads(page['text']) == fact
            with pytest.raises(ResultArchiveError):
                await archive.read(replace(context, trusted_context={**context.trusted_context, 'user_id': 'other'}), ref)
        # New current-task envelope replaces the old one; no requirement to
        # copy the previous large facts into the new goal merely to keep access.
        resumed, new_pin = delegated_working_input(kept, task(), 'continued')
        assert directory in resumed
        resumed.insert(0, HumanMessage(content='Old dialogue. ' * 1100))
        compact.pinned = new_pin
        second = await compact.abefore_model({'messages': resumed}, SimpleNamespace(context=context))
        second_dir = next(m for m in second['messages'] if m.id == SOURCE_INDEX_ID)
        assert json.loads(second_dir.content)['working_sources'] == entries
        assert model.calls == 2  # Only the two requested history summaries.
    asyncio.run(run())


def test_publication_navigation_survives_without_promoting_expired_fact():
    async def run():
        from tests.test_historical_context_budget import historical
        from application.business_observation import BusinessObservation
        from application.historical_context_budget import project_historical_payload
        _, entry = historical(12000)
        entry['observation']['facts'][0]['observed_at'] = '2019-12-31T00:00:00Z'
        entry['observation']['facts'][0]['valid_until'] = '2020-01-01T00:00:00Z'
        entry['observation_id'] = BusinessObservation.model_validate(entry['observation']).observation_id
        projected = project_historical_payload({'history': [entry]}, observation_path=('history',))
        messages, _ = delegated_working_input([], task(observations=projected['history']), 'initial')
        archive = TargetResultArchive(InMemoryStore())
        index = await working_source_index(messages, archive, _context())
        source = json.loads(index.content)['working_sources'][0]
        assert source['read_conversation_observation']['observation_id'] == entry['observation_id']
        assert source['valid_until'] == '2020-01-01T00:00:00Z'
        assert source['coverage']['deliverable'] == entry['observation']['coverage']['deliverable']
        assert 'value' not in source and 'value_json' not in source
        assert await working_source_index([index], archive, _context()) == index
    asyncio.run(run())
