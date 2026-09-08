"""Reuse frozen production compose input; compare a coverage instruction without new retrieval."""
import asyncio,json,os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import SystemMessage
from evaluation.answer_coverage_candidate import ANSWER_COVERAGE
from core.framework_models import framework_model
from core.model_policy import ModelPolicy,ModelProfile,ModelRole,ReasoningEffort
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from services.claim_verification import verify_claims
from dataclasses import asdict
OUT=Path('artifacts/eval/answer-coverage6-2026-09-08')
async def main():
 values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values);profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek');model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
 rows=[json.loads(l) for l in Path('artifacts/eval/rag-contract-full2-2026-09-08/run/full-cases.jsonl').read_text().splitlines()]
 class InjectCoverage:
  async def ainvoke(self,messages,**kwargs):
   return await model.ainvoke([SystemMessage(messages[0].content+'\n'+ANSWER_COVERAGE),*messages[1:]],**kwargs)
 p=OUT/'composition-v2.jsonl'
 if p.exists():raise ValueError('do not overwrite')
 for r in rows:
  payload=json.loads(r['api_calls'][2]['request']['messages'][0]['content']);original=json.loads(r['api_calls'][-1]['request']['messages'][0]['content'])
  cap=FrameworkCapture(limit=2);provider=AnthropicConversationPlanningProvider({ModelRole.SYNTHESIS:InjectCoverage()},model_profile=profile,synthesis_profile=profile,max_tokens=2048,callbacks=(cap,))
  result={'id':r['case_id'],'payload':payload,'baseline_answer':original['answer']}
  try:
   answer=await provider.compose(payload);assessment=await verify_claims(model,profile,question=original['question'],answer=answer,evidence=original['evidence'],callbacks=(cap,));result.update(answer=answer,assessment=asdict(assessment))
  except Exception as e:result['error']=type(e).__name__
  result['calls']=cap.calls
  assert ANSWER_COVERAGE in cap.calls[0]['request']['system'], 'candidate not applied'
  with p.open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
  print(result['id'],result.get('answer',result.get('error')),flush=True)
if __name__=='__main__':asyncio.run(main())
