"""Local full-union pointwise reranking replay; final packing budget stays fixed."""
import gzip
import json
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection, ranked, complete
from mcp.rank_fusion import fuse_rankings
from mcp.context_packer import ContextPacker, ContextCandidate


def main():
    root = Path('artifacts/eval')
    out = root / 'rag-candidate-budget-2026-09-07'
    out.mkdir(exist_ok=False)
    ds = RagDataset.load(root/'doc2dial-rag-mini-dev-v1', verify_checksum=True)
    cases = ds.select_cases('dev')
    lookup = {c.case_id:c for c in cases}
    _, chunks, texts = projection(ds.documents, cases, 512, 64, 'structure_aware')
    ids = [c.chunk_id for c in chunks]
    pos = {cid:i for i,cid in enumerate(ids)}
    by = {c.chunk_id:c for c in chunks}
    hitmap = {c.chunk_id:{'document_id':c.document_id,'source_start_char':c.start_char,'source_end_char':c.end_char} for c in chunks}
    arrays = np.load(root/'rag-local-parent-pair-v2-2026-09-07/scores.npz')
    dense, lex = arrays['dense'], arrays['lexical']
    tasks = []
    for i,c in enumerate(cases):
        routes = {'dense':ranked(dense[i],ids,40),'bm25':tuple(cid for cid in ranked(lex[i],ids,len(ids)) if lex[i,pos[cid]]>0)[:40]}
        tasks.append({'case_id':c.case_id,'query':c.query,'routes':routes,'arm':'raw300'})
    replay = root/'rag-resolved-query20-provider-v2-2026-09-07/retrieval.jsonl.gz'
    tasks.extend({**r,'arm':'resolved17'} for r in (json.loads(l) for l in gzip.decompress(replay.read_bytes()).splitlines()) if r['arm']=='resolved')
    path = '/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3'
    tok = AutoTokenizer.from_pretrained(path,local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(path,local_files_only=True,torch_dtype=torch.float16).to('cuda').eval()
    rows=[]; total_pairs=0; start=time.monotonic()
    for n,task in enumerate(tasks):
        c=lookup[task['case_id']]
        pool=fuse_rankings(task['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=80)
        scores=[]
        for off in range(0,len(pool),4):
            batch=pool[off:off+4]
            enc=tok([task['query']]*len(batch),[texts[pos[cid]] for cid in batch],padding=True,truncation=False,return_tensors='pt')
            assert enc['input_ids'].shape[1]<=8192
            with torch.inference_mode():scores.extend(model(**{k:v.to('cuda') for k,v in enc.items()}).logits.view(-1).float().cpu().tolist())
        total_pairs+=len(pool); score=dict(zip(pool,scores)); result={}
        for k in (20,40,80):
            candidates=pool[:k]; final=sorted(candidates,key=lambda cid:(-score[cid],cid))
            pack=ContextPacker().pack([ContextCandidate(cid,by[cid].document_id,by[cid].content,by[cid].start_char,by[cid].end_char) for cid in final[:5]],max_tokens=2600,max_chunks=5)
            assert pack.token_count<=2600
            result[str(k)]={'pool_count':len(candidates),'complete_pool':complete(c,candidates,by),'complete5':complete(c,final[:5],by),'packed_complete':complete(c,pack.chunk_ids,by),'tokens':pack.token_count,'selected':pack.chunk_ids,'metrics5':evaluate_ranked_hits(c,final,hitmap,top_k=5)}
        rows.append({'case_id':c.case_id,'arm':task['arm'],'query':task['query'],'union':pool,'scores':scores,'budgets':result})
        if n%30==0: print('completed',n+1,'/',len(tasks),flush=True)
    summary={'scope':'development local replay, not live PG/Agent answer acceptance','api_calls':0,'actual_pairs':total_pairs,'elapsed_ms':(time.monotonic()-start)*1000,'context_budget':2600,'max_chunks':5,'arms':{}}
    for arm in ('raw300','resolved17'):
        rs=[r for r in rows if r['arm']==arm]
        summary['arms'][arm]={str(k):{'cases':len(rs),'complete_pool':sum(r['budgets'][str(k)]['complete_pool'] for r in rs),'packed_complete':sum(r['budgets'][str(k)]['packed_complete'] for r in rs),'mrr5':sum(r['budgets'][str(k)]['metrics5']['mrr'] for r in rs)/len(rs),'pairs_required':sum(r['budgets'][str(k)]['pool_count'] for r in rs),'rescues_vs20':sum(not r['budgets']['20']['packed_complete'] and r['budgets'][str(k)]['packed_complete'] for r in rs),'harms_vs20':sum(r['budgets']['20']['packed_complete'] and not r['budgets'][str(k)]['packed_complete'] for r in rs)} for k in (20,40,80)}
    (out/'cases.jsonl.gz').write_bytes(gzip.compress(''.join(json.dumps(r)+'\n' for r in rows).encode(),mtime=0))
    (out/'report.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
