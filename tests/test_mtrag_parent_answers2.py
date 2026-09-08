"""Four paid calls are audited offline; mechanical checks are not semantic grading."""
import gzip,json,re
from pathlib import Path
from scripts.adapt_mtrag_retrieval_dataset import digest

def test_frozen_parent_answers_inputs_and_citations():
    root=Path('artifacts/eval/rag-g4-parent-answers2-2026-09-08')
    fixture=json.loads((root/'inputs.json').read_text());run=json.loads((root/'run.json').read_text())
    rows=[json.loads(l) for l in gzip.decompress((root/'answers.jsonl.gz').read_bytes()).decode().splitlines()]
    assert run['calls']==len(rows)==4 and run['fixture_sha256']==digest(root/'inputs.json')
    frozen={r['case_id']:r for r in fixture['rows']}
    assert len({(r['case_id'],r['arm']) for r in rows})==4
    for r in rows:
        expected=json.loads(frozen[r['case_id']]['arms'][r['arm']]['wire'])
        assert r['payload']['evidence']['facts'][0]['value']==expected
        assert r['payload']['current_message']==fixture['inputs'][r['case_id']]['current_message']
        assert r['payload']['conversation_context']==fixture['inputs'][r['case_id']]['conversation_context']
        ids={x['evidence_id'] for x in expected['evidence']}
        cited=set(re.findall(r'\[(E[0-9a-f]+)\]',r['answer']))
        assert cited and cited<=ids and r['answer'].strip() and len(r['calls'])==1
