"""Frozen candidate identity, exact pointwise ordering and publication-view metric audit."""
import gzip,json
from pathlib import Path
from scripts.run_mtrag_reranker_pair import at5,rerank
from scripts.adapt_mtrag_retrieval_dataset import digest

def test_parent_validation_preserves_candidates_scores_and_visible_metrics():
    root=Path('artifacts/eval/rag-g4-cloud-parent-validation8-2026-09-08')
    def read(p):return json.loads(gzip.decompress(p.read_bytes()))
    hybrid=read(root/'hybrid/cases.json.gz');ranked=read(root/'rerank/cases.json.gz');packed=read(root/'pack/cases.json.gz')
    scores={(s['case_index'],s['passage_id']):s['score'] for s in read(root/'rerank/scores.json.gz')}
    assert json.loads((root/'rerank/identity.json').read_text())['hybrid_sha256']==digest(root/'hybrid/cases.json.gz')
    assert len(hybrid)==len(ranked)==len(packed)==8
    for i,(h,r,p) in enumerate(zip(hybrid,ranked,packed,strict=True)):
        assert h['case_id']==r['case_id']==p['case_id']
        assert h['gold']==r['gold']==p['gold']
        for arm in ('dense','parent_local'):
            ids=h['variants'][arm]['ranking'];order=r['arms'][arm]['ranking'];view=p['arms'][arm]
            assert len(ids)==len(set(ids))==20
            assert order==rerank(ids,{pid:scores[i,pid] for pid in ids})
            assert r['arms'][arm]['after']==at5(order,set(h['gold']))
            assert view['packed_ids']==view['serialized_ids']
            assert len(view['serialized_ids'])<=5 and view['body_estimated_tokens']<=2600
            assert set(view['serialized_ids'])<=set(ids)
            assert view['metrics']==at5(view['serialized_ids'],set(h['gold']))
