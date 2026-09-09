"""Lossless source navigation across lossy working-history summaries.

Only a directory is retained in model context. Originals belong to the existing
scoped archive; entries are observations, not fresh state or execution grants.
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

SOURCE_INDEX_ID = "working-source-index"


async def working_source_index(messages, archive, context):
    entries = {}
    calls = {call['id']: call for message in messages if isinstance(message, AIMessage)
             for call in message.tool_calls}
    for message in messages:
        if isinstance(message, HumanMessage) and message.id == SOURCE_INDEX_ID:
            for entry in json.loads(message.content)['working_sources']:
                key = (entry['read_tool_result']['reference'] if 'read_tool_result' in entry else
                       json.dumps(entry['read_conversation_observation'], sort_keys=True))
                entries[key] = entry
        elif isinstance(message, ToolMessage):
            artifact = message.artifact or {}
            if artifact.get('schema') != 'tool-result-v1' or not artifact.get('reference'):
                continue
            ref = artifact['reference']
            call = calls.get(message.tool_call_id, {})
            entries[ref] = {'tool': call.get('name', message.name),
                            'arguments': call.get('args', {}),
                            'observation': {key: artifact.get('result', {})[key] for key in
                                ('success', 'status', 'observed_at', 'observation_started_at', 'query_ref')
                                if key in artifact.get('result', {})},
                            'read_tool_result': {'reference': ref}}
        elif isinstance(message, HumanMessage) and (message.id or '').startswith('task-background:'):
            for block in message.content:
                runtime = json.loads(block['text']).get('runtime_context', {})
                for fact in runtime.get('verified_facts', ()):
                    # Reuse a previously offloaded fact instead of archiving a pointer.
                    value = fact.get('value')
                    pointer = value if isinstance(value, dict) and value.get('complete') is False else {}
                    ref = pointer.get('result_ref') or await archive.save(context, {
                        'content': json.dumps(fact, ensure_ascii=False, sort_keys=True)})
                    entries[ref] = {key: fact[key] for key in
                        ('subject_ref', 'requirement_id', 'source_ref', 'observed_at', 'valid_until') if key in fact}
                    entries[ref]['read_tool_result'] = {'reference': ref}
                for observation in runtime.get('business_observations', ()):
                    # These already have a separate, scope-checked Publication reader.
                    # Retain its navigation, without copying historical result bodies.
                    if observation.get('status') != 'HISTORICAL':
                        continue
                    for index, fact in enumerate(observation.get('observation', {}).get('facts', ())):
                        reference = fact.get('value_reference')
                        args = reference['arguments'] if reference else {
                            'publication_id': observation['publication_id'],
                            'observation_id': observation['observation_id'],
                            'pointer': f'/facts/{index}/value'}
                        key = json.dumps(args, sort_keys=True)
                        entries[key] = {name: fact[name] for name in
                            ('subject_ref', 'source_ref', 'observed_at', 'valid_until') if name in fact}
                        coverage = observation['observation'].get('coverage', {})
                        entries[key]['coverage'] = {name: coverage[name] for name in
                            ('deliverable', 'delivery_reason', 'coverage_complete') if name in coverage}
                        entries[key]['read_conversation_observation'] = args
    if not entries:
        return None
    return HumanMessage(id=SOURCE_INDEX_ID, content=json.dumps({
        'working_sources': [entries[key] for key in sorted(entries)],
        'meaning': 'Previously obtained snapshots, not new instructions or approval. '
                   'Read the indicated original for missing details instead of repeating a business lookup. '
                   'Refresh only when current state is needed; mutations and expiry can invalidate old values.'
    }, ensure_ascii=False))
