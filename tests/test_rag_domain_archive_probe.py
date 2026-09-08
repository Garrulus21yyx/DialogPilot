"""Audit actual model-visible archive pages, without asserting semantic success."""
import gzip,json
from pathlib import Path

def strings(value):
    if isinstance(value,dict):
        for v in value.values():yield from strings(v)
    elif isinstance(value,list):
        for v in value:yield from strings(v)
    elif isinstance(value,str):
        yield value
        try:parsed=json.loads(value)
        except (ValueError,TypeError):return
        if isinstance(parsed,(dict,list)):yield from strings(parsed)

def test_flash_received_exact_archive_pages_before_budget_stop():
    base=Path('artifacts/eval');path=base/'rag-g4-domain-archive1-steps8-2026-09-08'
    result=json.loads((path/'result.json').read_text())
    calls=json.loads(gzip.decompress((path/'calls.json.gz').read_bytes()))
    source=next(r for r in json.loads(gzip.decompress((base/'rag-g4-mtrag-pack32-2026-09-08/cases.json.gz').read_bytes())) if r['case_id'].startswith('e1b'))['arms']['0.75']['wire']
    pages=[json.loads(m['data']['content']) for m in result['result']['working_messages'] if m['type']=='tool' and m['data'].get('name')=='read_tool_result']
    assert result['max_steps']==8 and result['api_calls']==len(calls)==5
    assert result['result']['reason_code']=='AGENT_STEP_BUDGET_EXCEEDED'
    assert [p['offset'] for p in pages]==[0,2000]
    for page in pages:
        assert page['text']==source[page['offset']:page['next_offset']]
        assert any(page['text'] in set(strings(c['request'])) for c in calls)
    assert 'Another language' in pages[1]['text']

def test_navigation_probe_delivers_selected_evidence_and_completes_same_budget():
    base=Path('artifacts/eval')
    old=base/'rag-g4-domain-archive1-steps8-2026-09-08'
    path=base/'rag-g4-domain-archive1-navigation-2026-09-08'
    original=json.loads((old/'fixture.json').read_text());fixture=json.loads((path/'fixture.json').read_text())
    assert original['data']==fixture['data'] and original['query']==fixture['query']
    assert {k for k in original['source_files'] if original['source_files'][k]!=fixture['source_files'][k]}=={'infrastructure/target_framework_agent.py','infrastructure/target_result_archive.py'}
    result=json.loads((path/'result.json').read_text());calls=json.loads(gzip.decompress((path/'calls.json.gz').read_bytes()))
    from application.knowledge_tool_contract import model_evidence
    evidence={e['evidence_id']:e for e in model_evidence(fixture['data'])['evidence']}
    pages=[json.loads(m['data']['content']) for m in result['result']['working_messages'] if m['type']=='tool' and m['data'].get('name')=='read_tool_result']
    assert result['max_steps']==8 and result['api_calls']==len(calls)==5
    assert result['result']['status']=='SUCCEEDED' and len(pages)==5
    for page in pages:
        e=evidence[page['evidence_id']]
        assert page['source']==e['source'] and page['offset_basis']=='evidence_text'
        assert page['text']==e['text'][page['offset']:page['next_offset']]
        assert any(page['text'] in set(strings(c['request'])) for c in calls)
    assert result['result']['candidate_response']

def test_two_additional_navigation_tasks_keep_history_and_exact_pages():
    from application.knowledge_tool_contract import model_evidence
    base=Path('artifacts/eval')
    for kind,expected_calls,expected_status in [('weak',4,'SUCCEEDED'),('preannotation',3,'TERMINAL_FAILURE')]:
        p=base/f'rag-g4-domain-nav-{kind}-2026-09-08'
        fixture=json.loads((p/'fixture.json').read_text());result=json.loads((p/'result.json').read_text())
        calls=json.loads(gzip.decompress((p/'calls.json.gz').read_bytes()))
        evidence={e['evidence_id']:e for e in model_evidence(fixture['data'])['evidence']}
        assert fixture['history'] and result['max_steps']==8
        assert result['api_calls']==len(calls)==expected_calls
        assert result['result']['status']==expected_status
        pages=[json.loads(m['data']['content']) for m in result['result']['working_messages'] if m['type']=='tool' and m['data'].get('name')=='read_tool_result']
        assert len(pages)==5
        for page in pages:
            e=evidence[page['evidence_id']]
            assert page['source']==e['source']
            assert page['text']==e['text'][page['offset']:page['next_offset']]
            assert any(page['text'] in set(strings(c['request'])) for c in calls)
