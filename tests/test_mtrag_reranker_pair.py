import gzip,json
from pathlib import Path
import pytest
from scripts.run_mtrag_reranker_pair import at5,rerank


def test_top5_metric_uses_top5_ideal_and_keeps_gold_denominator():
    gold={str(i) for i in range(8)}
    m=at5([str(i) for i in range(8)],gold)
    assert m=={'recall@5':5/8,'mrr@5':1.,'ndcg@5':1.}
    assert at5(['wrong','0'],gold)['mrr@5']==.5


def test_rerank_preserves_candidates_and_ties_are_input_order_independent():
    scores={'a':1.,'b':1.,'c':-2.}
    assert rerank(['c','b','a'],scores)==rerank(['a','b','c'],scores)==['a','b','c']


def test_real_scores_cover_exact_union_and_all_arms_preserve_candidates():
    root=Path('artifacts/eval')
    with gzip.open(root/'rag-g4-mtrag-hybrid32-2026-09-08/cases.json.gz','rt') as f:before=json.load(f)
    with gzip.open(root/'rag-g4-mtrag-rerank32-2026-09-08/cases.json.gz','rt') as f:after=json.load(f)
    with gzip.open(root/'rag-g4-mtrag-rerank32-2026-09-08/scores.json.gz','rt') as f:rows=json.load(f)
    lookup={(r['case_index'],r['passage_id']):r['score'] for r in rows}
    needed={(i,p) for i,r in enumerate(before) for arm in ('0.25','0.5','0.75') for p in r['variants'][arm]['ranking']}
    assert len(rows)==len(lookup)==1104 and set(lookup)==needed
    assert len(before)==len(after)==32
    for i,(a,b) in enumerate(zip(before,after)):
        assert a['case_id']==b['case_id'] and a['gold']==b['gold']
        for arm,v in b['arms'].items():
            assert set(v['ranking'])==set(a['variants'][arm]['ranking'])
            assert v['ranking']==rerank(a['variants'][arm]['ranking'],{p:lookup[i,p] for p in v['ranking']})
            assert v['after']==at5(v['ranking'],set(a['gold']))
            assert v['after']['recall@5']<=v['candidate_recall@20']
