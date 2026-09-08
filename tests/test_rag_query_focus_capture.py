"""Validate actual model-input provenance, not semantic accuracy by keyword matching."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path('artifacts/eval/rag-g2-query-focus4-2026-09-08')


def test_actual_context_capture_and_read_only_plans():
    cases = json.loads((ROOT / 'cases.json').read_text())
    rows = [json.loads(x) for x in gzip.decompress((ROOT / 'run/queries.jsonl.gz').read_bytes()).splitlines()]
    manifest = json.loads((ROOT / 'run/manifest.json').read_text())
    assert manifest['cases_sha256'] == hashlib.sha256((ROOT / 'cases.json').read_bytes()).hexdigest()
    assert manifest['api_calls'] == len(rows) == len(cases) == 4
    for case, row in zip(cases, rows):
        assert case['case_id'] == row['case_id']
        assert len(row['calls']) == 1
        call = row['calls'][0]
        assert not call.get('error_type')
        request = json.loads(call['request']['messages'][0]['content'])
        visible = request['conversation_context']['recent_messages']
        assert [(x['role'], x['content']) for x in visible] == [(x['role'], x['text']) for x in case['history']]
        assert case['query'] in call['request']['messages'][0]['content']
        items = row['plan']['work']['items']
        assert len(items) == 1
        assert items[0]['effect'] == 'READ'
        assert items[0]['allowed_tools'] == ['knowledge_search']
        assert len(row['resolved_queries']) == 1
