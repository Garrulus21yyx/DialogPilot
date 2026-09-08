"""Actual user raw + frozen generated standalone, fixed 20 CE / 5 evidence."""
import json,gzip,hashlib,argparse
from pathlib import Path
from scripts.run_rag_fresh100 import ROOT,mtrag
from scripts.replay_rag_rank_selection import read,pack
from scripts.audit_rag_fresh100 import data
from mcp.rank_fusion import fuse_rankings
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
OUT=Path('artifacts/eval/rag-raw-standalone300-2026-09-08')
def main(name):
    dest=OUT/name;dest.mkdir(exist_ok=False);selection=read(ROOT/'selection.json');old=read(ROOT/name/'cases.json.gz')
    if name=='Doc2Dial':
        generated=read('artifacts/eval/rag-pure-query100-2026-09-08/retrieval.json');gen={r['id']:{**r,'query':r['queries'][0],'result':r['agent']} for r in generated};ss=read('artifacts/eval/rag-pure-query100-2026-09-08/scores.json.gz');raw={r['id']:r for r in old};raw_result={r['id']:r['arms']['baseline'] for r in old}
    else:
        generated=read(f'artifacts/eval/rag-query-other200-2026-09-08/{name}/cases.json.gz');gen={r['id']:{**r,'result':r['model']} for r in generated};ss=read(f'artifacts/eval/rag-query-other200-2026-09-08/{name}/scores.json.gz');raw={r['id']:r for r in old};raw_result={r['id']:r['arms']['baseline'] for r in old}
    if name=='MTRAG':
        inputs={r['id']:r['messages'][-1]['content'] for r in read('artifacts/eval/rag-query-other200-2026-09-08/inputs.json') if r['dataset']==name}
        rows,rawsrc,rawtxt,rawmetric,identity=mtrag([{**c,'query':inputs[c['id']]} for c in selection[name]])
        raw={r['id']:{'id':r['id'],'query':r['query'],'routes':r['routes']} for r in rows};raw_result={}
    combined=[]
    for o in old:
        rid=o['id'];routes={f'raw_{k}':v for k,v in raw[rid]['routes'].items()};routes.update({f'standalone_{k}':v for k,v in gen[rid]['routes'].items()})
        union=list(dict.fromkeys(c for rr in routes.values() for c in rr));combined.append({**o,'pool':union})
    src,txt,metric,expected=data(name,combined,selection)
    lookup={}
    for s in ss:
        assert s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest();lookup[s['query'],s['cid']]=s['score']
    scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4);added=[];results=[]
    def score(q,pool):
        missing=[c for c in pool if (q,c) not in lookup]
        vv=scorer._score(q,[txt[c] for c in missing]) if missing else []
        for c,v in zip(missing,vv,strict=True):lookup[q,c]=v;added.append({'query':q,'cid':c,'text_sha256':hashlib.sha256(txt[c].encode()).hexdigest(),'score':v})
        assert len(added)<=4000
        return sorted(pool,key=lambda c:(-lookup[q,c],pool.index(c) if name=='WixQA' else c))
    for o in old:
        rid=o['id'];g=gen[rid];r=raw[rid];q=g['query'];rr={f'raw_{k}':v for k,v in r['routes'].items()};rr.update({f'standalone_{k}':v for k,v in g['routes'].items()})
        # Equal queries are one branch, not a duplicated vote.
        if r['query']==q:pool=list(fuse_rankings(g['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20))
        else:pool=list(fuse_rankings(rr,weights={'raw_dense':.125,'raw_bm25':.125,'standalone_dense':.375,'standalone_bm25':.375},rrf_k=10,top_k=20))
        ce=score(q,pool);ids,tokens=pack(q,ce,src);joint={**metric(o,ids),'ids':ids,'tokens':tokens}
        if name=='MTRAG':
            pp=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));order=score(r['query'],pp);ii,tt=pack(r['query'],order,src);raw_result[rid]={**metric(o,ii),'ids':ii,'tokens':tt}
        results.append({'id':rid,'raw_query':r['query'],'standalone_query':q,'routes':rr,'joint_pool':pool,'joint_ce':ce,'arms':{'raw':raw_result[rid],'standalone':g['result'],'joint':joint},'official_baseline':o['arms']['baseline'] if name=='MTRAG' else None})
    report={'n':100,'new_api_calls':0,'new_ce_pairs':len(added),'raw_weight':.25,'standalone_weight':.75,'summary':{a:{k:sum(r['arms'][a][k] for r in results)/100 for k in ('recall','mrr','ndcg')} for a in ('raw','standalone','joint')},'paired_vs_standalone':{'better':sum(r['arms']['joint']['recall']>r['arms']['standalone']['recall'] for r in results),'worse':sum(r['arms']['joint']['recall']<r['arms']['standalone']['recall'] for r in results)}}
    for n,v in [('cases',results),('added-scores',added)]: (dest/(n+'.json.gz')).write_bytes(gzip.compress(json.dumps(v).encode(),mtime=0))
    (dest/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('name',choices=['Doc2Dial','MTRAG','WixQA']);main(p.parse_args().name)
