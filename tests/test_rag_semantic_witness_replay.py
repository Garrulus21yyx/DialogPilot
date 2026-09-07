"""Audit verifier-only comparisons: evidence/question/schema must stay frozen."""
import gzip
import json
from pathlib import Path

ROOT=Path('artifacts/eval')


def test_all_semantic_runs_preserve_original_evidence_and_have_four_calls():
    frozen=json.loads(gzip.decompress((ROOT/'rag-g2-regression6-clean-2026-09-07/semantic-witnesses.json.gz').read_bytes()))
    by={r['case_id']:r for r in frozen}
    for arm in ('baseline','focused','controls'):
        root=ROOT/f'rag-semantic-witnesses-{arm}-2026-09-07'
        rows=json.loads(gzip.decompress((root/'rows.json.gz').read_bytes()))
        report=json.loads((root/'report.json').read_text())
        assert len(rows)==report['api_calls']==4
        for row in rows:
            assert len(row['calls'])==1
            before=by[row['case_id']]['verifier_input']
            original=json.loads(before['messages'][0]['content'])
            sent=row['calls'][0]['request']
            actual=json.loads(sent['messages'][0]['content'])
            assert actual['evidence']==original['evidence']
            assert actual['question']==original['question']
            assert actual['answer']==row['answer']
            assert sent['tools'][0]['input_schema']==before['tools'][0]['input_schema']
            if arm=='baseline':assert sent['system']==before['system']
            else:assert sent['system']==before['system']+report['added_instruction']
            assert row['output']==row['calls'][0]['raw_output']['tool_calls'][0]['args']['result']
            if arm=='controls':
                assert row['output']['supported']==(row['arm']=='supported_control')
                if not row['output']['supported']:assert row['output']['issues']
