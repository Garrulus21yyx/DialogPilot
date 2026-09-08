"""Complete .5 comparison from existing route/CE scores; no model calls."""
import gzip,json,hashlib
from pathlib import Path
from mcp.rank_fusion import fuse_rankings
from scripts.run_mtrag_reranker_pair import rerank,at5
from scripts.run_mtrag_lexical_query_pair import metrics

def read(p):return json.loads(gzip.decompress(p.read_bytes()))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    old=Path('artifacts/eval/rag-g4-mtrag-heldout35-2026-09-08')
    root=Path('artifacts/eval/rag-mtrag-balanced35-2026-09-08');root.mkdir(exist_ok=False)
    routes=read(old/'retrieval/cases.json.gz');hybrid=read(old/'hybrid/cases.json.gz');scores=read(old/'rerank/scores.json.gz');identity=json.loads((old/'rerank/identity.json').read_text())
    assert identity['hybrid_sha256']==sha(old/'hybrid/cases.json.gz')
    lookup={(s['case_index'],s['passage_id']):s['score'] for s in scores};assert len(lookup)==len(scores)
    hs=[];results=[]
    for i,(r,h) in enumerate(zip(routes,hybrid,strict=True)):
        assert all(r[k]==h[k] for k in ('query','case_id','gold','group_id','domain'))
        variants={};arms={}
        for a in (.25,.5,.75):
            ids=fuse_rankings(r['routes'],weights={'dense':a,'bm25':1-a},rrf_k=10,top_k=20)
            if str(a) in h['variants']:assert ids==h['variants'][str(a)]['ranking']
            ordered=rerank(ids,{p:lookup[i,p] for p in ids})
            recall=len(set(ids)&set(r['gold']))/len(set(r['gold']))
            variants[str(a)]={'ranking':ids,'metrics':{'recall@20':recall}}
            arms[str(a)]={'ranking':ordered,'before':at5(ids,set(r['gold'])),'after':at5(ordered,set(r['gold'])),'candidate_recall@20':recall}
        hs.append({**h,'variants':variants});results.append({k:r[k] for k in ('case_id','group_id','domain','gold')}|{'arms':arms})
    for folder,data in [('hybrid',hs),('rerank',results)]:
        (root/folder).mkdir();(root/folder/'cases.json.gz').write_bytes(gzip.compress(json.dumps(data).encode(),mtime=0))
    (root/'rerank/identity.json').write_text(json.dumps({**identity,'hybrid_sha256':sha(root/'hybrid/cases.json.gz'),'reused_scores_sha256':sha(old/'rerank/scores.json.gz'),'recipe':'Exact recorded query/passage scores; zero new scores. Original identity retained except hybrid digest.'},indent=2)+'\n')
    (root/'audit.json').write_text(json.dumps({'cases':len(results),'api_calls':0,'new_model_scores':0,'old_hybrid_equivalent':True,'same_query_gold':True,'input_routes_sha256':sha(old/'retrieval/cases.json.gz'),'input_scores_sha256':sha(old/'rerank/scores.json.gz')},indent=2)+'\n')
    print('35 cases, all three arms, zero new scores')
if __name__=='__main__':main()
