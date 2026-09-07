"""Audit captured production Context -> model -> query and PG evidence lineage."""
import gzip
import json
from pathlib import Path

ROOT=Path('artifacts/eval')


def read(name):
    return [json.loads(line) for line in gzip.decompress((ROOT/name/'queries.jsonl.gz').read_bytes()).splitlines()]


def test_captured_model_receives_prepared_history_roles_and_current_query():
    rows=read('rag-g2-context-miss2-2026-09-07')+read('rag-g2-context-ecommerce2-2026-09-07')
    assert len(rows)==4
    for row in rows:
        assert len(row['calls'])==1
        payload=json.loads(row['calls'][0]['request']['messages'][0]['content'])
        assert payload['message']==row['raw_query']
        actual=payload['conversation_context']['recent_messages']
        prepared=row['context']['recent_messages']
        assert [(m['role'],m['content']) for m in actual]==[(m['role'],m['content']) for m in prepared]
        original=list(zip(row['source_roles'],map(str.strip,row['history'])))
        assert [(m['role'],m['content'].strip()) for m in actual]==original[-len(actual):]
        work=(row['plan'].get('work') or {}).get('items',[])
        queries=[json.loads(arg['value_json']) for item in work if item['allowed_tools']==['knowledge_search'] for arg in item['arguments'] if arg['name']=='query']
        assert queries==row['resolved_queries']
    unsupported=rows[1]
    assert unsupported['plan']['route']['mode']=='OUT_OF_SCOPE'
    assert not unsupported['resolved_queries']
    for row in rows[2:]:
        assert len(row['resolved_queries'])==1
        assert all(item['effect']=='READ' and item['allowed_tools']==['knowledge_search'] for item in row['plan']['work']['items'])


def test_pg_replay_uses_exact_agent_query_and_complete_source_spans():
    captures=read('rag-g2-context-miss2-2026-09-07')
    rows=[json.loads(line) for line in gzip.decompress((ROOT/'rag-g2-candidate-replay-2026-09-07/cases.jsonl.gz').read_bytes()).splitlines()]
    actual=[r for r in rows if r['mode']=='actual_agent']
    assert len(actual)==1 and actual[0]['query']==captures[0]['resolved_queries'][0]
    snapshot=json.loads(gzip.decompress((ROOT/'rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    cases={c['id']:c for c in map(json.loads,snapshot['cases.jsonl'].splitlines())}
    for row in rows:
        assert row['candidate_status']=='OK'
        assert len(row['candidates'])<=20
        order=row['candidates'];gold=cases[row['case_id']]['evidence']
        ranks=[next((i for i,c in enumerate(order,1) if c['source_id']==g['document_id'] and c['source_start_char']<=g['start_char'] and c['source_end_char']>=g['end_char']),None) for g in gold]
        assert ranks==row['rankings']['production_fused20']['evidence_ranks']
    assert actual[0]['rankings']['production_fused20']['all_evidence_rank']==1
