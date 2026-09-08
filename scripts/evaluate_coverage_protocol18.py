"""Frozen old6 + new12 protocol comparison; no retrieval, no per-case retuning."""
import asyncio,json,os,hashlib,argparse
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from core.structured_model import structured_call
from evaluation.framework_capture import FrameworkCapture
from evaluation.coverage_protocol_candidate import SCHEMA,SYSTEM,aggregate
from services.claim_verification import verify_claims,make_request
from dataclasses import asdict
OUT=Path('artifacts/eval/coverage-protocol18-2026-09-08')

def cases():
 old=json.loads(Path('artifacts/eval/answer-coverage6-2026-09-08/manifest.json').read_text())['cases']
 for c in old:c.update(split='development',expected_supported=True)
 triples=[
 ('reset_gap','重置要怎么操作？','设备应先关机。[E1] 资料没有说明接下来的复位操作，我无法确认完整步骤。','重置前先关闭设备。',True,True),
 ('eligibility','谁可以借这台仪器？','通过培训的成员可以借用。[E1]','通过培训的成员可以借用仪器。',True,True),
 ('procedure_omitted','这台仪器怎么借？','通过培训的成员可以借用。[E1]','通过培训的成员可以借用仪器。',False,True),
 ('price_only','服务收费多少？','每次十元。[E1]','每次服务十元。',True,True),
 ('two_parts','服务收费多少，在哪里预约？','每次十元。[E1]','每次服务十元。',False,True),
 ('two_parts_gap','服务收费多少，在哪里预约？','每次十元。[E1] 当前资料未说明预约入口。','每次服务十元。',True,True),
 ('invented_steps','如何提交材料？','在个人中心点击“提交”即可。[E1]','材料须在周五前提交。',True,False),
 ('scope_negation','哪些设备不兼容X？','A设备不兼容X。[E1]','A设备不兼容X。',True,True),
 ('wrong_polarity','哪些设备不兼容X？','A设备不兼容X。[E1]','A设备兼容X。',True,False),
 ('status_not_reason','包裹为什么延误？','包裹仍在运输中。[E1]','包裹仍在运输中，延误原因尚无记录。',False,True),
 ('reason_gap','包裹为什么延误？','包裹仍在运输中，但记录没有说明延误原因。[E1]','包裹仍在运输中，延误原因尚无记录。',True,True),
 ('full_steps','如何启动设备？','先接电，再按启动键。[E1]','启动方法：先接电，再按启动键。',True,True)]
 for i,q,a,text,answered,supported in triples:old.append({'id':i,'split':'new_validation','request':make_request(q,a,{'approval_required':False,'context':{'sources':[{'evidence_id':'E1','text':text}],'coverage':{'complete':True}}}),'expected_answered':answered,'expected_supported':supported})
 return old

async def main(phase):
 if phase=='freeze':
  OUT.mkdir(parents=True,exist_ok=False);(OUT/'manifest.json').write_text(json.dumps({'cases':cases(),'candidate_system':SYSTEM,'schema':SCHEMA,'candidate_source_sha256':hashlib.sha256(Path('evaluation/coverage_protocol_candidate.py').read_bytes()).hexdigest(),'budget':36,'model':'deepseek-v4-flash','adoption':'all known incomplete cases detected, no valid-case false rejection, no unsupported new-case acceptance; otherwise do not migrate','scope':'self-contained requests only; isolated verifier evaluation; previous cases consumed, validation newly authored'},ensure_ascii=False,indent=2)+'\n');return
 manifest=json.loads((OUT/'manifest.json').read_text());assert manifest['candidate_source_sha256']==hashlib.sha256(Path('evaluation/coverage_protocol_candidate.py').read_bytes()).hexdigest()
 dest=OUT/(phase+'.jsonl')
 if dest.exists():raise ValueError('do not overwrite')
 values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values);profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek');model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
 for c in manifest['cases']:
  if c['split']!=phase:continue
  for arm in ['baseline','candidate']:
   cap=FrameworkCapture(limit=1);r={'id':c['id'],'arm':arm}
   try:
    if arm=='baseline':
     a=await verify_claims(model,profile,**c['request'],callbacks=(cap,));r['output']=asdict(a);r['result']={'supported':a.supported,'answered':a.answered,'publishable':a.supported and a.answered}
    else:
     r['output']=await structured_call(model,name='submit_claim_checks',schema=SCHEMA,system=SYSTEM,messages=[HumanMessage(json.dumps(c['request'],ensure_ascii=False))],callbacks=(cap,));r['result']=aggregate(c['request'],r['output'])
   except Exception as e:r['error']=type(e).__name__;r['error_reason']=str(e)[:200]
   r['calls']=cap.calls
   with dest.open('a') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
   print(c['id'],arm,r.get('result',r.get('error')),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['freeze','development','new_validation']);asyncio.run(main(p.parse_args().phase))
