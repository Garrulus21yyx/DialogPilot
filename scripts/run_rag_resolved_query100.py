"""Single frozen manual-history query contrast; raw baseline must reproduce."""
import gzip,json,hashlib,time
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer
from scripts.run_rag_fresh100 import ROOT,MODEL,route_rows,release
from scripts.audit_rag_fresh100 import data
from scripts.replay_rag_rank_selection import read,digest,pack
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_provider_free import projection,complete
from evaluation.local_bge_m3_retrieval_eval import bm25_matrix
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from mcp.rank_fusion import fuse_rankings
OUT=Path('artifacts/eval/rag-resolved-query100-2026-09-08')
def main():
    assert not (OUT/'report.json').exists()
    manifest=read(OUT/'manifest.json');assert digest(OUT/'queries.json')==manifest['query_sha256']
    queries=read(OUT/'queries.json');old=read(ROOT/'Doc2Dial/cases.json.gz');ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True)
    _,chunks,texts=projection(ds.documents,ds.cases,512,64,'structure_aware');ids=[c.chunk_id for c in chunks];byid=dict(zip(ids,chunks))
    src,txt,metric,expected=data('Doc2Dial',old,read(ROOT/'selection.json'))
    for q,c,r in zip(queries,ds.cases,old,strict=True):assert q['id']==c.case_id==r['id'] and q['query']==c.query==r['query'] and q['history']==list(c.history)
    start=time.monotonic();model=SentenceTransformer(str(MODEL),device='cuda',local_files_only=True)
    vec=model.encode(texts,batch_size=16,normalize_embeddings=True,convert_to_numpy=True)
    qq=[q['query'] for q in queries]+[q['resolved_query'] for q in queries]
    qv=model.encode(qq,batch_size=16,normalize_embeddings=True,convert_to_numpy=True);release(model)
    routes=route_rows(qq,ids,qv@vec.T,bm25_matrix(qq,texts))
    for r,o in zip(routes[:100],old,strict=True):assert r==o['routes'], 'Raw routes changed'
    scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4)
    assert scorer.identity==read(ROOT/'Doc2Dial/identity.json')['reranker']
    results=[];ss=[]
    for i,(q,c,o,r) in enumerate(zip(queries,ds.cases,old,routes[100:],strict=True)):
        union=list(fuse_rankings(r,weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40));pool=union[:20]
        scores=scorer._score(q['resolved_query'],[txt[x] for x in pool]);lookup=dict(zip(pool,scores,strict=True));ce=sorted(pool,key=lambda x:(-lookup[x],x))
        selected,tokens=pack(q['resolved_query'],ce,src);m=metric(o,selected)
        ss.extend({'id':q['id'],'cid':cid,'query':q['resolved_query'],'text_sha256':hashlib.sha256(txt[cid].encode()).hexdigest(),'score':s} for cid,s in lookup.items())
        stages={'dense20':r['dense'],'bm25_20':r['bm25'],'union40':union,'fusion20':pool,'ce5':ce[:5],'wire5':selected}
        results.append({'id':q['id'],'query':q['resolved_query'],'ambiguity_note':q['ambiguity_note'],'routes':r,'ce_order':ce,'stages':{k:float(complete(c,v,byid)) for k,v in stages.items()},'baseline':o['arms']['baseline'],'resolved':{**m,'ids':selected,'tokens':tokens}})
        if i%20==0:print('scored',i,flush=True)
    for name,obj in [('cases',results),('scores',ss)]: (OUT/(name+'.json.gz')).write_bytes(gzip.compress(json.dumps(obj).encode(),mtime=0))
    report={'n':100,'api_calls':0,'new_ce_pairs':len(ss),'raw_routes_reproduced':True,'query_sha256':manifest['query_sha256'],'wall_seconds':time.monotonic()-start,
            'metrics':{a:{k:sum(r[a][k] for r in results)/100 for k in ('recall','mrr','ndcg')} for a in ('baseline','resolved')},
            'resolved_stages':{k:sum(r['stages'][k] for r in results)/100 for k in stages},
            'rescued':[r['id'] for r in results if r['resolved']['recall']>r['baseline']['recall']],
            'harmed':[r['id'] for r in results if r['resolved']['recall']<r['baseline']['recall']],
            'identity':scorer.identity,'scope':'Consumed manual-query diagnostic; not real Agent or generated answer accuracy; no synonyms expansion.'}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='identity'},indent=2))
if __name__=='__main__': main()
