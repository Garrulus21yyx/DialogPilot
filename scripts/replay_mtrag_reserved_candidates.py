"""One preregistered dev-only candidate reservation; reuse complete pointwise scores."""
import gzip
import json
from pathlib import Path
from scripts.replay_mtrag_hybrid_weights import paired_rows, sha
from scripts.run_mtrag_reranker_pair import at5, rerank
from mcp.rank_fusion import fuse_rankings

BASE=Path('artifacts/eval')
def read(path):
    with gzip.open(path,'rt') as f:return json.load(f)

def main():
    dense=BASE/'rag-g4-mtrag-dense-complete-2026-09-08/results.json.gz'
    lexical=BASE/'rag-g4-mtrag-lexical32-2026-09-07/results.json.gz'
    hybrid=BASE/'rag-g4-mtrag-hybrid32-2026-09-08/cases.json.gz'
    ce=BASE/'rag-g4-mtrag-rerank32-2026-09-08'
    ds=read(dense);bs=read(lexical);rows=paired_rows(ds,bs)
    old=read(hybrid)
    assert rows==old, 'upstream identity mismatch'
    assert json.loads((ce/'identity.json').read_text())['hybrid_sha256']==sha(hybrid)
    scores={(r['case_index'],r['passage_id']):r['score'] for r in read(ce/'scores.json.gz')}
    bm={r['case_id']:r for r in bs if r['mode']=='rewrite'}
    output=[]
    for i,(d,r) in enumerate(zip(ds,rows)):
        routes={'dense':[x['id'] for x in d['ranking'][:20]],'lexical':[x['id'] for x in bm[d['case_id']]['ranking'][:20]]}
        ordered=fuse_rankings(routes,weights={'dense':.75,'lexical':.25},rrf_k=10,top_k=40)
        reserved=set(routes['dense'][:5])|set(routes['lexical'][:5])
        selected=set(reserved)
        for pid in ordered:
            if len(selected)>=20:break
            selected.add(pid)
        ranking=[p for p in ordered if p in selected]
        assert reserved<=set(ranking) and len(ranking)==min(20,len(ordered))
        variants={a:r['variants'][a]['ranking'] for a in ('0.25','0.5','0.75')}
        variants['reserve5']=ranking
        arms={}
        for a,ids in variants.items():
            ranked=rerank(ids,{p:scores[i,p] for p in ids})
            arms[a]={'candidate_recall@20':len(set(ids)&set(r['gold']))/len(r['gold']),'after':at5(ranked,set(r['gold'])),'candidates':ids,'ranking':ranked}
        output.append({'case_id':r['case_id'],'gold':r['gold'],'arms':arms})
    summary={a:{'candidate_recall@20':sum(r['arms'][a]['candidate_recall@20'] for r in output)/len(output),**{m:sum(r['arms'][a]['after'][m] for r in output)/len(output) for m in ('recall@5','mrr@5','ndcg@5')}} for a in variants}
    paired={a:{m:{'better':sum(r['arms']['reserve5']['after'][m]>r['arms'][a]['after'][m]+1e-12 for r in output),'worse':sum(r['arms']['reserve5']['after'][m]<r['arms'][a]['after'][m]-1e-12 for r in output)} for m in ('recall@5','mrr@5','ndcg@5')} for a in ('0.25','0.5','0.75')}
    report={'scope':'Consumed dev32; same query/source20/candidate20, shared pointwise CE; no pack or answers','api_calls':0,'new_model_scores':0,'summary':summary,'paired_reserve_vs_fixed':paired,'hashes':{str(p):sha(p) for p in (dense,lexical,hybrid,ce/'scores.json.gz',ce/'identity.json')}}
    dest=BASE/'rag-g4-reserve5-dev32-2026-09-08';dest.mkdir(exist_ok=False)
    with gzip.open(dest/'cases.json.gz','wt') as f:json.dump(output,f)
    (dest/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
