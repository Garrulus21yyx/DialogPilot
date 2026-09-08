"""Deterministic evidence metrics; semantic business scores require separate review."""
import argparse,json,math,hashlib
from pathlib import Path
from scripts.build_ecommerce_rag_acceptance import ROOT

def main():
 p=argparse.ArgumentParser();p.add_argument('--runs',nargs='+',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--split',choices=['dev','heldout'],default='dev');p.add_argument('--flat',action='store_true');args=p.parse_args();gold={r['id']:r for r in json.loads((ROOT/(args.split+'.gold.json')).read_text())};seen=set();cases=[];calls=0;input_tokens=output_tokens=cache_tokens=0
 for run in args.runs:
  for line in (run/('full-cases.jsonl' if args.flat else 'runtime/full-cases.jsonl')).read_text().splitlines():
   r=json.loads(line);cid=r['case_id'];assert cid in gold and cid not in seen;seen.add(cid);g=gold[cid];sources=[];queries=[]
   for t in r['tools']:
    if t['name']=='knowledge_search':
     queries.append(t['params'].get('query'));d=t['result'].get('data') or {};d=json.loads(d) if isinstance(d,str) else d
     wire=t['result'].get('output_for_model')
     wire=json.loads(wire) if isinstance(wire,str) else (wire or {})
     for item in wire.get('evidence',[]):
      sid=item['source']['source_id']
      original=next(x for x in d['evidence_pack']['items'] if x['source_ref']['source_id']==sid and x['source_ref']['start_char']==item['source']['start_char'])
      assert original['text']==item['text']
      if sid not in sources:sources.append(sid)
   target=set(g['knowledge_sources']);hits=[i for i,s in enumerate(sources[:5],1) if s in target];recall=len(set(sources[:5])&target)/len(target);ideal=sum(1/math.log2(i+1) for i in range(1,min(5,len(target))+1));ndcg=sum(1/math.log2(i+1) for i in hits)/ideal
   resp=r.get('outcome',{}).get('response',{});api=r['api_calls'];calls+=len(api)
   for c in api:
    u=c.get('usage') or (c.get('response') or {}).get('usage',{});cached=(u.get('input_token_details') or {}).get('cache_read',0) if c.get('usage') else u.get('cache_read_input_tokens',0);cache_tokens+=cached;input_tokens+=u.get('input_tokens',0)+(0 if c.get('usage') else cached+u.get('cache_creation_input_tokens',0));output_tokens+=u.get('output_tokens',0)
   cases.append({'id':cid,'tools':[t['name'] for t in r['tools']],'queries':queries,'visible_source_ids':sources,'source_recall_at5':recall,'source_ndcg_at5':ndcg,'source_mrr_at5':1/hits[0] if hits else 0,'answer':resp.get('response'),'runtime_verified':resp.get('verified',False),'business_required':g['business_required'],'business_lookup_called':any(t['name']=='order_lookup' for t in r['tools']),'api_calls':len(api),'requirements':g['answer_requirements'],'semantic_review':'PENDING'})
 report={'n':len(cases),'source_recall_at5':sum(c['source_recall_at5'] for c in cases)/len(cases),'source_ndcg_at5':sum(c['source_ndcg_at5'] for c in cases)/len(cases),'api_calls':calls,'input_tokens_including_cache':input_tokens,'cache_read_tokens':cache_tokens,'reported_output_tokens':output_tokens,'business_correctness':None,'limitations':'First five unique sources across tool calls in call order; source-level retrieval only; runtime verified is not semantic gold. Semantic review pending. Usage excludes unreported provider tokens.','cases':cases}
 args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print({k:v for k,v in report.items() if k!='cases'})
if __name__=='__main__':main()
