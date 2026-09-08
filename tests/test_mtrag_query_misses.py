import gzip,json
from pathlib import Path
from scripts.run_mtrag_lexical_query_pair import metrics


def test_query_only_replay_preserves_gold_and_original_baseline():
    root=Path('artifacts/eval')
    with gzip.open(root/'rag-g4-query-miss4-replay-2026-09-08/cases.json.gz','rt') as f:rs=json.load(f)
    with gzip.open(root/'rag-g4-mtrag-dense-complete-2026-09-08/results.json.gz','rt') as f:baseline={r['case_id']:r for r in json.load(f)}
    manual=json.loads((root/'rag-g4-query-miss4-2026-09-08/queries.json').read_text())['cases']
    assert len(rs)==12 and len(manual)==4
    for m in manual:
        subset=[r for r in rs if r['case_id']==m['case_id']]
        assert {r['mode'] for r in subset}=={'official','role_stripped','context_authored'}
        for r in subset:
            assert r['gold']==baseline[r['case_id']]['gold']
            for route,ids in r['routes'].items():
                assert len(ids)<=20 and r['diagnostic'][route]['metrics']==metrics(ids,set(r['gold']))
            if r['mode']=='official':
                assert r['query']==baseline[r['case_id']]['query']
                assert r['diagnostic']['dense']['metrics']==baseline[r['case_id']]['metrics']
                assert r['diagnostic']['bm25']['metrics']['recall@20']==0
            if r['mode']=='context_authored':assert r['query']==m['authored_query']
    report=json.loads((root/'rag-g4-query-miss4-replay-2026-09-08/report.json').read_text())
    assert report['api_calls']==report['document_embeddings']==0
