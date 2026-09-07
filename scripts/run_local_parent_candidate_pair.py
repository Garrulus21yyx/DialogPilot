"""Bounded parent-within-document refill vs flat on identical cached local scores."""
import argparse,json,time
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection,complete,ranked,parent_child_candidates,sha
from evaluation.local_bge_m3_retrieval_eval import bm25_matrix
from mcp.rank_fusion import fuse_rankings


def main():
 p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,required=True);args=p.parse_args()
 args.output.mkdir(parents=True,exist_ok=False);args.cache.mkdir(parents=True,exist_ok=True)
 ds=RagDataset.load(args.dataset,verify_checksum=True);cases=ds.select_cases('dev')
 _,chunks,texts=projection(ds.documents,cases,512,64,'structure_aware');ids=[c.chunk_id for c in chunks];byid={c.chunk_id:c for c in chunks};qs=[c.query for c in cases]
 model=None;vectors=[]
 for label,values in [('documents',texts),('queries',qs)]:
  key=sha({'model':str(args.model),'input':values,'dtype':'float32','normalize':True});cache=args.cache/(key+'.npy')
  if cache.exists():v=np.load(cache)
  else:
   if model is None:model=SentenceTransformer(str(args.model),device='cuda',local_files_only=True)
   v=model.encode(values,batch_size=8,normalize_embeddings=True,show_progress_bar=False,convert_to_numpy=True);np.save(cache,v)
  vectors.append(v)
 dense=vectors[1]@vectors[0].T;lex=bm25_matrix(qs,texts);np.savez_compressed(args.output/'scores.npz',dense=dense,lexical=lex)
 hitmap={c.chunk_id:{'document_id':c.document_id,'source_start_char':c.start_char,'source_end_char':c.end_char} for c in chunks}
 rows=[]
 for i,case in enumerate(cases):
  routes={'dense':ranked(dense[i],ids,20),'bm25':tuple(cid for cid in ranked(lex[i],ids,len(ids)) if lex[i,ids.index(cid)]>0)[:20]}
  flat=fuse_rankings(routes,weights={'dense':.25,'bm25':.75},rrf_k=10,top_k=20)
  parent,trace=parent_child_candidates(flat,routes,byid,dense[i],lex[i],ids,.25,20)
  rows.append({'case_id':case.case_id,'flat_complete20':complete(case,flat,byid),'parent_complete20':complete(case,parent,byid),
    'flat_metrics':evaluate_ranked_hits(case,flat,hitmap,top_k=20),'parent_metrics':evaluate_ranked_hits(case,parent,hitmap,top_k=20),
    'flat':flat,'parent':parent,'routes':routes,'parent_trace':trace})
 summary={'scope':'LOCAL_EXACT_DENSE_PYTHON_BM25_CANDIDATES_NOT_POSTGRES_NOT_AGENT','cases':len(cases),'documents':len(ds.documents),'chunks':len(chunks),'api_calls':0,'candidate_budget':20,'global_reserved':10,'parent_limit':3,'query':'raw','dense_weight':.25,'rrf_k':10,
 'flat_complete20':sum(r['flat_complete20'] for r in rows),'parent_complete20':sum(r['parent_complete20'] for r in rows),
 'rescues':sum(not r['flat_complete20'] and r['parent_complete20'] for r in rows),'harms':sum(r['flat_complete20'] and not r['parent_complete20'] for r in rows),
 'dataset_checksums':ds.manifest,'model':str(args.model),'extra_work':'additional local ranking over selected documents; same final pool is not identical compute','excluded':['reranker','packing','generation','child-to-parent expansion']}
 (args.output/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');(args.output/'predictions.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows));print(json.dumps(summary,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
