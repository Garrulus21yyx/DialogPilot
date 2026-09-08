import gzip,json
from pathlib import Path
from services.answer_verifier import _binding


def test_frozen_verifier_requests_bind_full_inputs():
    root=Path('artifacts/eval/rag-g4-verifier4-2026-09-08')
    inputs={r['case_id']:r for r in json.loads((root/'inputs.json').read_text())}
    rows=[json.loads(l) for l in gzip.decompress((root/'results.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows)==4
    assert {(r['case_id'],r['arm']) for r in rows}=={(cid,a) for cid in inputs for a in ('original','repaired')}
    for r in rows:
        e=inputs[r['case_id']]
        assert len(r['calls'])==1 and not r['calls'][0].get('error_type')
        assert r['answer']==e[r['arm']]
        assert r['result']['request_binding']==_binding(e['question'],e[r['arm']],json.dumps(e['context'],ensure_ascii=False),task_plan=None,coverage=None,knowledge_evidence=e['knowledge_evidence'],agent_outcomes=e['agent_outcomes'])
        assert r['result']['assessment'] is not None
