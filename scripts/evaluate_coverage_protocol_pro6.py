"""Fixed schema/input model-only diagnostic; never switches production profile."""
import asyncio,json,os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from core.structured_model import structured_call
from evaluation.framework_capture import FrameworkCapture
from evaluation.coverage_protocol_candidate import SCHEMA,SYSTEM,aggregate
ROOT=Path('artifacts/eval/coverage-protocol18-2026-09-08')
async def main():
 m=json.loads((ROOT/'manifest.json').read_text());assert m['candidate_system']==SYSTEM and m['schema']==SCHEMA
 dest=ROOT/'pro-development.jsonl'
 if dest.exists():raise ValueError('do not overwrite')
 values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values);profile=ModelProfile('deepseek-v4-pro',ReasoningEffort.NONE,'deepseek');model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
 for c in m['cases']:
  if c['split']!='development':continue
  cap=FrameworkCapture(limit=1);r={'id':c['id'],'arm':'pro_candidate'}
  try:
   r['output']=await structured_call(model,name='submit_claim_checks',schema=SCHEMA,system=SYSTEM,messages=[HumanMessage(json.dumps(c['request'],ensure_ascii=False))],callbacks=(cap,));r['result']=aggregate(c['request'],r['output'])
  except Exception as e:r.update(error=type(e).__name__,error_reason=str(e)[:200])
  r['calls']=cap.calls
  with dest.open('a') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
  print(r['id'],r.get('result',r.get('error')),flush=True)
if __name__=='__main__':asyncio.run(main())
