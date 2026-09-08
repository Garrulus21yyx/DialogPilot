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
