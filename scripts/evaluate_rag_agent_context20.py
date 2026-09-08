"""Fixed-backend replay of actual Agent queries, including no-query outcomes."""
import json,gzip,hashlib
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer
from scripts.run_rag_agent_context20 import OUT
from scripts.run_rag_fresh100 import ROOT,MODEL,route_rows,release
from scripts.replay_rag_rank_selection import read,pack
from scripts.audit_rag_fresh100 import data
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_provider_free import projection,complete
from evaluation.local_bge_m3_retrieval_eval import bm25_matrix
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from mcp.rank_fusion import fuse_rankings

def main(output=OUT):
    OUT = output
    assert not (OUT/'retrieval.json').exists()
    rows=read(OUT/'captures.jsonl',True);assert len(rows)==len(read(OUT/'manifest.json')['ids'])
    ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True);cases={c.case_id:c for c in ds.cases}
    old=read(ROOT/'Doc2Dial/cases.json.gz');manual={r['id']:r for r in read('artifacts/eval/rag-resolved-query100-2026-09-08/cases.json.gz')}
    src,txt,metric,expected=data('Doc2Dial',old,read(ROOT/'selection.json'))
    _,chunks,texts=projection(ds.documents,ds.cases,512,64,'structure_aware');ids=[c.chunk_id for c in chunks];byid=dict(zip(ids,chunks))
    valid=[r for r in rows if len(r['queries'])==1 and isinstance(r['queries'][0],str) and r['queries'][0].strip()]
    if valid:
        encoder=SentenceTransformer(str(MODEL),device='cuda',local_files_only=True);v=encoder.encode(texts,batch_size=16,normalize_embeddings=True,convert_to_numpy=True)
        qq=[r['queries'][0] for r in valid];qv=encoder.encode(qq,batch_size=16,normalize_embeddings=True,convert_to_numpy=True);release(encoder)
        routes=route_rows(qq,ids,qv@v.T,bm25_matrix(qq,texts));rr=dict(zip([r['id'] for r in valid],routes))
    else:rr={}
    scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4);results=[];ss=[]
    for row in rows:
        cid=row['id'];r={'id':cid,'queries':row['queries'],'raw':manual[cid]['baseline'],'manual':manual[cid]['resolved']}
        if cid not in rr:r.update(agent={'recall':0.,'mrr':0.,'ndcg':0.,'ids':[]},status='NO_SINGLE_QUERY',union_recall=0.,candidate_recall=0.,stages={k:0. for k in ('dense20','bm25_20','union40','fusion20','ce5','wire5')})
        else:
            query=row['queries'][0];union=list(fuse_rankings(rr[cid],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40));pool=union[:20]
            scores=scorer._score(query,[txt[x] for x in pool]);lookup=dict(zip(pool,scores));ce=sorted(pool,key=lambda x:(-lookup[x],x));selected,tokens=pack(query,ce,src)
            ss.extend({'id':cid,'cid':x,'query':query,'text_sha256':hashlib.sha256(txt[x].encode()).hexdigest(),'score':s} for x,s in lookup.items())
            r.update(agent={**metric(row,selected),'ids':selected,'tokens':tokens},status='SINGLE_QUERY',routes=rr[cid],ce_order=ce,union_recall=float(complete(cases[cid],union,byid)),candidate_recall=float(complete(cases[cid],pool,byid)))
        if cid in rr:
            r['stages']={k:float(complete(cases[cid],v,byid)) for k,v in {'dense20':rr[cid]['dense'],'bm25_20':rr[cid]['bm25'],'union40':union,'fusion20':pool,'ce5':ce[:5],'wire5':selected}.items()}
        results.append(r)
    report={'n':len(rows),'single_query':len(valid),'calls':sum(len(r['calls']) for r in rows),'new_ce_pairs':len(ss),'stages':{k:sum(r['stages'][k] for r in results)/len(rows) for k in ('dense20','bm25_20','union40','fusion20','ce5','wire5')},'summary':{a:{k:sum(r[a][k] for r in results)/len(rows) for k in ('recall','mrr','ndcg')} for a in ('raw','manual','agent')},'union_recall':sum(r['union_recall'] for r in results)/len(rows),'candidate_recall':sum(r['candidate_recall'] for r in results)/len(rows),'memory_triggered':sum(r.get('loaded_context',{}).get('memory_attempted',False) for r in rows),'summary_envelopes_present':sum(r.get('loaded_context',{}).get('summary') is not None for r in rows),'nonempty_summary_chunks':sum(bool(json.loads(r['loaded_context']['summary']['content']).get('chunks')) for r in rows if r.get('loaded_context',{}).get('summary')),'error_types':[r['error_type'] for r in rows if 'error_type' in r]}
    (OUT/'retrieval.json').write_text(json.dumps(results,indent=2)+'\n');(OUT/'scores.json.gz').write_bytes(gzip.compress(json.dumps(ss).encode(),mtime=0));(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUT);main(parser.parse_args().output)
