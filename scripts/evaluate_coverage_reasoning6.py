"""Diagnostic structured coverage decomposition, not a production verifier replacement."""
import asyncio,json,os
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from core.structured_model import structured_call
from langchain_core.messages import HumanMessage
from evaluation.framework_capture import FrameworkCapture
from evaluation.answer_coverage_candidate import ANSWER_COVERAGE
OUT=Path('artifacts/eval/answer-coverage6-2026-09-08')
SCHEMA={'type':'object','additionalProperties':False,'required':['needs'],'properties':{'needs':{'type':'array','minItems':1,'maxItems':8,'items':{'type':'object','additionalProperties':False,'required':['requested_information','evidence_provides','answer_provides','missing_information','covered'],'properties':{**{k:{'type':'string'} for k in ['requested_information','evidence_provides','answer_provides','missing_information']},'covered':{'type':'boolean'}}}}}}
async def main():
 cases=json.loads((OUT/'manifest.json').read_text())['cases'];values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values);profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek');model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=2048)
 dest=OUT/'decomposition.jsonl'
 if dest.exists():raise ValueError('do not overwrite')
 for c in cases:
  cap=FrameworkCapture(limit=1);row={'id':c['id']}
  try:
   row['output']=await structured_call(model,name='assess_coverage',schema=SCHEMA,system=ANSWER_COVERAGE+' For each user information need, first describe what evidence provides and what the answer provides. Then identify any unanswered detail. covered is true only if the answer provides the requested information or explicitly explains that detail is unavailable. An empty missing_information alone is not proof. Do not evaluate fact support here; assess coverage only.',messages=[HumanMessage(json.dumps(c['request'],ensure_ascii=False))],callbacks=(cap,))
   row['answered']=all(n['covered'] for n in row['output']['needs'])
  except Exception as e:row['error']=type(e).__name__
  row['calls']=cap.calls
  with dest.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
  print(c['id'],row.get('answered'),flush=True)
if __name__=='__main__':asyncio.run(main())
