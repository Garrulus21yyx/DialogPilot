"""Same-evidence baseline/candidate semantic coverage probes; 12 Flash calls max."""
import asyncio, json, os
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelProfile,ModelRole,ReasoningEffort
from core.framework_models import framework_model
from evaluation.answer_coverage_candidate import ANSWER_COVERAGE
from evaluation.framework_capture import FrameworkCapture
import services.claim_verification as cv
OUT=Path('artifacts/eval/answer-coverage6-2026-09-08')
async def main():
 OUT.mkdir(parents=True,exist_ok=False)
 old=[json.loads(l) for l in Path('artifacts/eval/rag-contract-full2-2026-09-08/run/full-cases.jsonl').read_text().splitlines() if json.loads(l)['case_id']=='switch'][0]
 request=json.loads(old['api_calls'][-1]['request']['messages'][0]['content'])
 def simple(i,q,a,text,expected):return {'id':i,'request':cv.make_request(q,a,{'approval_required':False,'context':{'sources':[{'evidence_id':'E1','text':text}],'coverage':{'complete':True}}}), 'expected_answered':expected}
 cases=[{'id':'original_invoice','request':request,'expected_answered':False},
 {'id':'explicit_gap','request':{**request,'answer':'电子发票可在订单完成后申请。[E243253281322] 当前资料没有说明申请入口和具体操作步骤，我无法据此确认该怎样提交申请。'},'expected_answered':True},
 simple('steps_missing','如何重置设备？','设备必须先关机。[E1]','重置前必须关机。',False),
 simple('steps_complete','如何重置设备？','先关机，再按住复位键五秒，松开后开机。[E1]','重置步骤：关机，按住复位键五秒，松开后开机。',True),
 simple('condition_only_requested','申请条件是什么？','须年满十八岁。[E1]','申请者须年满十八岁。',True),
 simple('compound_missing','申请条件是什么，多久办好？','须年满十八岁。[E1]','申请者须年满十八岁。办理时限不详。',False)]
 baseline=cv.SYSTEM
 (OUT/'manifest.json').write_text(json.dumps({'cases':cases,'baseline_system':baseline,'candidate_addition':ANSWER_COVERAGE,'budget':12,'model':'deepseek-v4-flash','scope':'frozen verifier-only development cases, no retrieval or business execution'},ensure_ascii=False,indent=2)+'\n')
 values={**dotenv_values('.env'),**os.environ}; policy=ModelPolicy.from_env(values);profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek');model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
 try:
  for c in cases:
   for arm in ('baseline','candidate'):
    cv.SYSTEM=baseline if arm=='baseline' else baseline+'\n'+ANSWER_COVERAGE
    cap=FrameworkCapture(limit=1);row={'id':c['id'],'arm':arm}
    try:row['assessment']=asdict(await cv.verify_claims(model,profile,**c['request'],callbacks=(cap,)))
    except Exception as e:row['error']=type(e).__name__
    row['calls']=cap.calls
    with (OUT/'results.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(c['id'],arm,row.get('assessment',{}).get('answered'),flush=True)
 finally:cv.SYSTEM=baseline
if __name__=='__main__':asyncio.run(main())
