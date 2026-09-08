"""Zero-provider replay of body, serialized evidence and archived request budgets.

Counterfactual request sizes are SDK estimates, not provider usage or measured
Agent completion. Replace every archived knowledge view in the first request
containing it, retaining other request messages and tool schema overhead.
"""
from __future__ import annotations
import copy
import gzip
import hashlib
import json
from pathlib import Path
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from core.token_estimator import TokenEstimator

SOURCE = Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
OUTPUT = Path('artifacts/eval/rag-evidence-budget-boundary-2026-09-08/report.json')
PROBES = ['rag-g4-domain-archive1-navigation-2026-09-08',
          'rag-g4-domain-nav-weak-2026-09-08',
          'rag-g4-domain-nav-preannotation-2026-09-08']


def request_estimate(request):
    messages = []
    for row in request['messages']:
        content = row['content']
        if row['role'] in ('ai', 'assistant'):
            messages.append(AIMessage(content=content))
        elif row['role'] == 'tool':
            messages.append(ToolMessage(content=content, tool_call_id='replay'))
        else:
            messages.append(HumanMessage(content=content))
    # Mirrors runtime schema shape. Provider serialization may differ; report
    # this explicitly as a reconstruction, never as exact input tokens.
    schemas = [{'name': t['name'], 'description': t.get('description', ''),
                'parameters': t['input_schema']} for t in request['tools']]
    return count_tokens_approximately(messages + [
        SystemMessage(content=request['system']),
        HumanMessage(content=json.dumps(schemas, ensure_ascii=False))])


def main():
    rows = json.loads(gzip.decompress(SOURCE.read_bytes()))
    views = []
    for row in rows:
        for arm, value in row['arms'].items():
            view = json.loads(value['wire'])
            body = sum(TokenEstimator.estimate(e['text']) for e in view['evidence'])
            wire = count_tokens_approximately([ToolMessage(content=value['wire'], tool_call_id='replay')])
            views.append({'case_id': row['case_id'], 'arm': arm,
                          'body_estimate': body, 'wire_sdk_estimate': wire,
                          'archive_at_2840': wire > 2840})
    probes = []
    for name in PROBES:
        root = Path('artifacts/eval') / name
        fixture = json.loads((root/'fixture.json').read_text())
        case = next(r for r in rows if json.loads(r['arms']['0.75']['wire'])['query_used'] == fixture['data']['evidence_pack']['query'])
        wire = case['arms']['0.75']['wire']
        calls = json.loads(gzip.decompress((root/'calls.json.gz').read_bytes()))
        for index, call in enumerate(calls):
            request = call['request']
            alternative = copy.deepcopy(request)
            replaced = 0
            for msg in alternative['messages']:
                if msg['role'] != 'tool' or not isinstance(msg['content'], str):
                    continue
                try:
                    pointer = json.loads(msg['content'])
                except ValueError:
                    continue
                if isinstance(pointer, dict) and pointer.get('evidence_directory'):
                    msg['content'] = wire
                    replaced += 1
            if replaced:
                before, after = request_estimate(request), request_estimate(alternative)
                probes.append({'probe': name, 'request_index': index,
                               'replaced_pointers': replaced,
                               'archived_request_sdk_estimate': before,
                               'full_evidence_request_sdk_estimate': after,
                               'available': 14200, 'full_fits': after <= 14200,
                               'trace_sha256': hashlib.sha256((root/'calls.json.gz').read_bytes()).hexdigest()})
                break
        else:
            raise AssertionError(f'No knowledge pointer found: {name}')
    report = {'scope': __doc__, 'api_calls': 0,
              'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'views': views, 'requests': probes,
              'summary': {'views': len(views), 'archived': sum(v['archive_at_2840'] for v in views),
                          'max_body': max(v['body_estimate'] for v in views),
                          'max_wire': max(v['wire_sdk_estimate'] for v in views)}}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'summary': report['summary'], 'requests': probes}, indent=2))


if __name__ == '__main__':
    main()
