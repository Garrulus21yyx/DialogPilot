import gzip,json
from pathlib import Path


def test_frozen_answer_inputs_and_complete_pair_capture():
    root=Path('artifacts/eval/rag-g4-mtrag-answer8-2026-09-08')
    fixture=json.loads((root/'inputs.json').read_text())
    rows=[json.loads(x) for x in gzip.decompress((root/'answers.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows)==16
    assert {(r['case_id'],r['arm']) for r in rows}=={(r['case_id'],a) for r in fixture['rows'] for a in r['arms']}
    for r in rows:
        assert len(r['calls'])==1 and not r['calls'][0].get('error_type')
        assert r['answer'].strip() and r['answer']!='CAPTURE_ONLY_NOT_AN_ANSWER'
        payload=r['payload'];f=fixture['inputs'][r['case_id']]
        assert payload['current_message']==f['current_message']
        assert payload['conversation_context']==f['conversation_context']
        message=r['calls'][0]['request']['messages'][0]['content']
        assert json.loads(message)==payload
