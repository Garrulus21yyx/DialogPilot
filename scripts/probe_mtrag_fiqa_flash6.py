"""First six FiQA cases in frozen order, same 20 candidates; diagnostic only."""
import asyncio,json,os
from dataclasses import asdict
from dotenv import dotenv_values
from anthropic import AsyncAnthropic
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from mcp.result_reranker import ResultReranker,RerankCandidate
from scripts.run_rag_tool_calibration import CaptureClient
from scripts.diagnose_mtrag_resolved100 import OUT,BASE
from scripts.replay_rag_rank_selection import read,pack
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
async def main():
 dest=OUT/'flash6.jsonl'
 existing=read(dest,True) if dest.exists() else []
 completed={r['id'] for r in existing}
 diag=read(OUT/'cases.json.gz');ids=[r['id'] for r in diag if r['domain']=='fiqa'][:6];rows=[r for r in read(BASE/'cases.json.gz') if r['id'] in ids]
 (OUT/'flash6-manifest.json').write_text(json.dumps({'ids':ids,'selection':'first six FiQA in existing case order, not heldout','max_calls':12,'candidate_k':20,'final_k':5,'context_budget':2600,'query_unchanged':True})+'\n')
 src,txt,metric,_=data('MTRAG',rows,read(ROOT/'selection.json'));values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values);profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek');transport=AsyncAnthropic(api_key=values['ANTHROPIC_API_KEY'],base_url=policy.base_url,max_retries=0);client=CaptureClient(transport,limit=12);ranker=ResultReranker(client,profile)
 try:
  for r in rows:
   if r['id'] in completed:continue
   start=len(client.calls);rank=await ranker.rerank(r['query'],[RerankCandidate(d,txt[d]) for d in r['pool']]);ii,tt=pack(r['query'],rank.ordered_ids,src)
   row={'id':r['id'],'query':r['query'],'pool':r['pool'],'rank':asdict(rank),'baseline':r['model'],'flash':{**metric(r,ii),'ids':ii,'tokens':tt},'calls':client.calls[start:]}
   with dest.open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=lambda x: {"sdk_sentinel":type(x).__name__})+'\n')
   print(r['id'],row['baseline']['recall'],row['flash']['recall'],rank.error,flush=True)
 finally:await transport.close()
if __name__=='__main__':asyncio.run(main())
