"""Cache-only fusion/window selection then local full-input CE. Development, not acceptance."""
import argparse,gzip,hashlib,json,math,re,time
from pathlib import Path
import numpy as np
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection,ranked,complete
from mcp.rank_fusion import fuse_rankings
from mcp.context_packer import ContextCandidate,ContextPacker
from core.token_estimator import TokenEstimator


def bucket(q):
    return 'identifier' if re.search(r'\d',q) else 'short' if len(q.split())<=4 else 'natural'
def fold(group):return int(hashlib.sha256(group.encode()).hexdigest()[:8],16)%5

def main():
 p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--scores',type=Path,required=True);p.add_argument('--reranker',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 args.output.mkdir(parents=True,exist_ok=False)
 ds=RagDataset.load(args.dataset,verify_checksum=True);cases=ds.select_cases('dev');_,chunks,texts=projection(ds.documents,cases,512,64,'structure_aware')
 ids=[c.chunk_id for c in chunks];byid={c.chunk_id:c for c in chunks};docs={d.document_id:d for d in ds.documents};positions={cid:i for i,cid in enumerate(ids)}
 scores=np.load(args.scores);dense,lex=scores['dense'],scores['lexical'];assert dense.shape==lex.shape==(len(cases),len(ids))
 hitmap={cid:{'document_id':c.document_id,'source_start_char':c.start_char,'source_end_char':c.end_char} for cid,c in byid.items()}
 routes=[{'dense':ranked(dense[i],ids,40),'bm25':tuple(cid for cid in ranked(lex[i],ids,len(ids)) if lex[i,positions[cid]]>0)[:40]} for i in range(len(cases))]
 configs=[(w,k) for w in (0,.25,.5,.75,1) for k in (10,30,60)]
 ranks={c:[fuse_rankings(r,weights={'dense':c[0],'bm25':1-c[0]},rrf_k=c[1],top_k=20) for r in routes] for c in configs}
 outcome={c:[bool(complete(case,rank,byid)) for case,rank in zip(cases,ranks[c])] for c in configs}
 def best(indices):return max(configs,key=lambda c:(sum(outcome[c][i] for i in indices),-abs(c[0]-.25),-c[1]))
 allidx=list(range(len(cases)));chosen=best(allidx);oof=[]
 for f in range(5):
  train=[i for i,c in enumerate(cases) if fold(c.group_id)!=f];test=[i for i,c in enumerate(cases) if fold(c.group_id)==f];globalc=best(train)
  typed={}
  for b in ('identifier','short','natural'):
   ix=[i for i in train if bucket(cases[i].query)==b];typed[b]=best(ix) if len(ix)>=20 else globalc
  for i in test:oof.append({'case_id':cases[i].case_id,'fold':f,'bucket':bucket(cases[i].query),'fixed':globalc,'adaptive':typed[bucket(cases[i].query)],'fixed_complete':outcome[globalc][i],'adaptive_complete':outcome[typed[bucket(cases[i].query)]][i]})
 fusion={'source_k':40,'candidate_k':20,'scores_sha256':hashlib.sha256(args.scores.read_bytes()).hexdigest(),'best_dev':chosen,'grid':[{'dense_weight':w,'rrf_k':k,'complete20':sum(outcome[(w,k)])} for w,k in configs],
 'oof':{'fixed_complete':sum(r['fixed_complete'] for r in oof),'adaptive_complete':sum(r['adaptive_complete'] for r in oof),'rescues':sum(not r['fixed_complete'] and r['adaptive_complete'] for r in oof),'harms':sum(r['fixed_complete'] and not r['adaptive_complete'] for r in oof)},'oof_cases':oof}
 (args.output/'fusion.json').write_text(json.dumps(fusion,indent=2)+'\n');print('FUSION',chosen,fusion['oof'],flush=True)
 neighbors={}
 for d in docs:
  ordered=sorted([c for c in chunks if c.document_id==d],key=lambda c:c.start_char)
  for j,c in enumerate(ordered):neighbors[c.chunk_id]=ordered[max(0,j-1):j+2]
 def packed(case,rank,mode):
  cs=[]
  for n,cid in enumerate(rank[:5]):
   c=byid[cid];d=docs[c.document_id];start,end=c.start_char,c.end_char
   if mode=='adjacent':start=min(x.start_char for x in neighbors[cid]);end=max(x.end_char for x in neighbors[cid])
   elif mode=='parent':start,end=0,len(d.content)
   text=d.content[start:end]
   if TokenEstimator.estimate(text)>2600:start,end,text=c.start_char,c.end_char,c.content
   cs.append(ContextCandidate(cid,c.document_id,text,start,end,score=1/(n+1)))
  pack=ContextPacker().pack(cs,max_tokens=2600,max_chunks=5)
  good=all(any(c.document_id==e.document_id and c.start_char<=e.start_char and c.end_char>=e.end_char for c in pack.selected) for e in case.evidence)
  assert pack.token_count<=2600
  return {'complete':good,'tokens':pack.token_count,'selected':[{'document_id':c.document_id,'start':c.start_char,'end':c.end_char} for c in pack.selected]}
 def evaluate(rankings,label):
  rows=[]
  for case,rank in zip(cases,rankings):
   metrics=evaluate_ranked_hits(case,rank,hitmap,top_k=5)
   rows.append({'case_id':case.case_id,'metrics5':metrics,**{m:packed(case,rank,m) for m in ('anchor','adjacent','parent')}})
  report={'label':label,'case_count':len(cases),'budget':2600,'budget_tokenizer':'production TokenEstimator, not API tokenizer','max_chunks':5,'oversized_expansion':'fall back to original anchor','modes':{m:{'complete':sum(r[m]['complete'] for r in rows),'mean_tokens':sum(r[m]['tokens'] for r in rows)/len(rows),'rescues_vs_anchor':sum(not r['anchor']['complete'] and r[m]['complete'] for r in rows),'harms_vs_anchor':sum(r['anchor']['complete'] and not r[m]['complete'] for r in rows)} for m in ('anchor','adjacent','parent')},'mrr5':sum(r['metrics5']['mrr'] for r in rows)/len(rows),'ndcg5':sum(r['metrics5']['ndcg'] for r in rows)/len(rows)}
  (args.output/(label+'.json')).write_text(json.dumps(report,indent=2)+'\n');(args.output/(label+'-cases.jsonl.gz')).write_bytes(gzip.compress(''.join(json.dumps(r)+'\n' for r in rows).encode(),mtime=0));print(label,report,flush=True)
  return report
 evaluate(ranks[(.25,10)],'current-fusion');before=evaluate(ranks[chosen],'selected-fusion')
 # Fixed same candidate pool. Full query and child input; never 1200-char prefix.
 import torch
 from transformers import AutoTokenizer,AutoModelForSequenceClassification
 tok=AutoTokenizer.from_pretrained(str(args.reranker),local_files_only=True)
 model=AutoModelForSequenceClassification.from_pretrained(str(args.reranker),local_files_only=True,torch_dtype=torch.float16).to('cuda').eval()
 locations=[(i,cid) for i,rank in enumerate(ranks[chosen]) for cid in rank];values=[];start=time.monotonic();maxlen=0
 for offset in range(0,len(locations),4):
  batch=locations[offset:offset+4];encoded=tok([cases[i].query for i,cid in batch],[texts[positions[cid]] for i,cid in batch],padding=True,truncation=False,return_tensors='pt')
  length=encoded['input_ids'].shape[1];maxlen=max(maxlen,length)
  if length>8192:raise ValueError('reranker input exceeds declared full-input budget')
  with torch.inference_mode():values.extend(model(**{k:v.to('cuda') for k,v in encoded.items()}).logits.view(-1).float().cpu().tolist())
  if offset%800==0:print('RERANK',offset,'/',len(locations),flush=True)
 ce={loc:v for loc,v in zip(locations,values)};reranked=[sorted(rank,key=lambda cid:(-ce[(i,cid)],cid)) for i,rank in enumerate(ranks[chosen])]
 after=evaluate(reranked,'local-crossencoder');np.savez_compressed(args.output/'crossencoder-scores.npz',values=values)
 paired=[]
 for left,right in [('current-fusion','selected-fusion'),('selected-fusion','local-crossencoder'),('current-fusion','local-crossencoder')]:
  saved=[]
  for label in (left,right):
   saved.append({r['case_id']:r['anchor']['complete'] for r in (json.loads(line) for line in gzip.decompress((args.output/(label+'-cases.jsonl.gz')).read_bytes()).splitlines())})
  a,b=saved;rescues=sum(not a[cid] and b[cid] for cid in a);harms=sum(a[cid] and not b[cid] for cid in a)
  paired.append({'before':left,'after':right,'rescues':rescues,'harms':harms,'delta_pp':100*(rescues-harms)/len(cases)})
 (args.output/'paired-changes.json').write_text(json.dumps(paired,indent=2)+'\n')
 (args.output/'manifest.json').write_text(json.dumps({'scope':'LOCAL_DEVELOPMENT_NOT_LIVE_AGENT','dataset':ds.manifest,'api_calls':0,'reranker':str(args.reranker),'reranker_model_sha256':hashlib.sha256((args.reranker/'model.safetensors').read_bytes()).hexdigest(),'pairs':len(locations),'max_input_tokens':maxlen,'rerank_ms':(time.monotonic()-start)*1000,'candidate_config':chosen,'candidate_orders':ranks[chosen],'phases':['fusion','context-expansion','rerank'],'not_run':['MTRAG','WixQA','query rewriting','generation','Flash comparison']},indent=2)+'\n')

if __name__=='__main__':main()
