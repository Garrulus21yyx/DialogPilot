"""Same retrieval backend and budgets for actual model queries on MTRAG/WixQA."""
import argparse,json,gzip,hashlib
from scripts.run_rag_other_query200 import OUT
from scripts.run_rag_fresh100 import ROOT,mtrag,wix
from scripts.replay_rag_rank_selection import read,pack
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from mcp.rank_fusion import fuse_rankings

def main(name):
    out=OUT/name;out.mkdir(exist_ok=False)
    cap=[r for r in read(OUT/'captures.jsonl',True) if r['dataset']==name];assert len(cap)==100
    sel=read(ROOT/'selection.json')[name];queries={r['id']:r for r in cap};old={r['id']:r for r in read(ROOT/name/'cases.json.gz')}
    valid=[{**c,'query':queries[c['id']]['queries'][0]} for c in sel if len(queries[c['id']]['queries'])==1]
    rows,src,txt,metric,identity=(mtrag if name=='MTRAG' else wix)(valid)
    scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4);assert scorer.identity==read(ROOT/name/'identity.json')['reranker']
    output={};ss=[]
    for i,r in enumerate(rows):
        union=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40));pool=union[:20]
        scores=scorer._score(r['query'],[txt[c] for c in pool]);lookup=dict(zip(pool,scores));tie=(lambda c:pool.index(c)) if name=='WixQA' else (lambda c:c)
        ce=sorted(pool,key=lambda c:(-lookup[c],tie(c)));ids,tokens=pack(r['query'],ce,src)
        def recall(candidates):
            found={src[c].document_id for c in candidates} if name=='WixQA' else set(candidates)
            return len(found&r['gold'])/len(r['gold'])
        stages={k:recall(v) for k,v in {'dense20':r['routes']['dense'],'bm25_20':r['routes']['bm25'],'union40':union,'fusion20':pool,'ce5':ce[:5],'wire5':ids}.items()}
        output[r['id']]={'id':r['id'],'query':r['query'],'routes':r['routes'],'pool':pool,'ce_order':ce,'stages':stages,'baseline':old[r['id']]['arms']['baseline'],'model':{**metric(r,ids),'ids':ids,'tokens':tokens}}
        ss.extend({'id':r['id'],'cid':c,'query':r['query'],'text_sha256':hashlib.sha256(txt[c].encode()).hexdigest(),'score':s} for c,s in lookup.items())
        if i%25==0:print(name,'reranked',i,flush=True)
    results=[output.get(c['id'],{'id':c['id'],'error_type':queries[c['id']].get('error_type','NO_QUERY'),'baseline':old[c['id']]['arms']['baseline'],'model':{'recall':0.,'mrr':0.,'ndcg':0.,'ids':[]},'stages':{k:0. for k in ('dense20','bm25_20','union40','fusion20','ce5','wire5')}}) for c in sel]
    report={'n':100,'valid_outputs':len(valid),'model_calls':sum(len(r['calls']) for r in cap),'ce_pairs':len(ss),'stages':{k:sum(r['stages'][k] for r in results)/100 for k in results[0]['stages']},'summary':{a:{k:sum(r[a][k] for r in results)/100 for k in ('recall','mrr','ndcg')} for a in ('baseline','model')},'better':sum(r['model']['recall']>r['baseline']['recall'] for r in results),'worse':sum(r['model']['recall']<r['baseline']['recall'] for r in results),'identity':identity}
    for n,v in [('cases',results),('scores',ss)]: (out/(n+'.json.gz')).write_bytes(gzip.compress(json.dumps(v).encode(),mtime=0))
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('dataset',choices=['MTRAG','WixQA']);main(p.parse_args().dataset)
