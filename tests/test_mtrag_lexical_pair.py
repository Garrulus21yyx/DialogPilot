import random
import numpy as np
from scripts.run_mtrag_lexical_query_pair import stream_bm25,metrics,select,DOMAINS
from evaluation.local_bge_m3_retrieval_eval import bm25_matrix


def test_stream_statistics_match_existing_bm25_across_generated_corpora():
    rng=random.Random(20260907)
    vocab=['refund','not','sku-2','return','policy','退款','no','123']
    for n in (1,7,51):
        docs=[' '.join(rng.choices(vocab,k=rng.randrange(1,80))) for _ in range(n)]
        queries=[' '.join(rng.choices(vocab+['absent'],k=rng.randrange(1,15))) for _ in range(13)]
        ids,actual=stream_bm25(queries,enumerate(docs))
        expected=bm25_matrix(queries,docs)
        assert ids==list(range(n))
        np.testing.assert_allclose(actual,expected,rtol=2e-6,atol=2e-6)


def test_metric_denominator_and_order():
    m=metrics(['wrong','a'],{'a','b'})
    assert m['recall@1']==0 and m['recall@5']==.5 and m['mrr@20']==.5
    assert 0<m['ndcg@20']<.5
    assert metrics([],{'a'})['ndcg@20']==0
    assert metrics(['a','b'],{'a','b'})['ndcg@20']==1


def test_selection_is_group_safe_order_independent_and_ignores_gold():
    cases=[{'id':f'{d}-{g}-{t}','group_id':f'{d}-{g}','split':'dev' if g<10 else 'heldout',
            'query_types':[d],'evidence':[]} for d in DOMAINS for g in range(12) for t in range(3)]
    chosen=select(cases,8)
    altered=[{**c,'evidence':[{'fake':'different gold'}]} for c in reversed(cases)]
    assert [c['id'] for c in chosen]==[c['id'] for c in select(altered,8)]
    assert len(chosen)==len({c['group_id'] for c in chosen})==32
    assert all(c['split']=='dev' for c in chosen)


def test_actual_pair_is_same_cases_domains_and_budget():
    import gzip,json
    from pathlib import Path
    root=Path('artifacts/eval/rag-g4-mtrag-lexical32-2026-09-07')
    with gzip.open(root/'results.json.gz','rt') as f:rs=json.load(f)
    report=json.loads((root/'report.json').read_text())
    chosen=json.loads((root/'selection.json').read_text())['cases']
    assert len(chosen)==len({c['group_id'] for c in chosen})==32
    assert all(c['split']=='dev' for c in chosen)
    assert len(rs)==96
    for c in chosen:
        matches=[r for r in rs if r['case_id']==c['id']]
        assert {r['mode'] for r in matches}=={'lastturn','questions','rewrite'}
        for r in matches:
            ids=[d['id'] for d in r['ranking']]
            assert len(ids)<=20 and len(ids)==len(set(ids))
            assert all(d.startswith('mtrag:'+r['domain']+':') for d in ids)
            assert r['gold']==sorted(e['document_id'] for e in c['evidence'])
            assert r['metrics']==metrics(ids,set(r['gold']))
            assert all(d['score']>0 for d in r['ranking'])
    for mode,summary in report['summary']['all'].items():
        for k,v in summary.items():
            assert abs(v-sum(r['metrics'][k] for r in rs if r['mode']==mode)/32)<1e-12
