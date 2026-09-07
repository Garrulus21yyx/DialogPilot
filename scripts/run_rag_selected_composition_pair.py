"""Small frozen public-dev composition replay using production compose provider, not full Agent QA."""
import argparse,asyncio,gzip,hashlib,json,os
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelRole
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from application.composition_output import render_composition
from application.response_assembly import AllowedClaim
from evaluation.rag_pipeline.dataset import RagDataset


def clean(v):
 if isinstance(v,list):return [clean(x) for x in v if not isinstance(x,dict) or x.get('type') not in ('thinking','redacted_thinking')]
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items() if k not in ('thinking','signature')}
 return v

async def run(args):
 args.output.mkdir(parents=True,exist_ok=False);ds=RagDataset.load(args.dataset,verify_checksum=True);docs={d.document_id:d for d in ds.documents}
 groups={}
 for c in ds.select_cases('dev'):groups.setdefault(c.group_id,[]).append(c)
 groupids=sorted(groups,key=lambda g:hashlib.sha256(('rag-composition20-v1\0'+g).encode()).hexdigest())[:20]
 cases=[max(groups[g],key=lambda c:(len(c.history),c.case_id)) for g in groupids]
 inputs={}
 for arm,label in [('baseline','current-fusion'),('candidate','local-crossencoder')]:
  rows=[json.loads(l) for l in gzip.decompress((args.stages/(label+'-cases.jsonl.gz')).read_bytes()).splitlines()];inputs[arm]={r['case_id']:r for r in rows}
 values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ);policy=ModelPolicy.from_env(values);profile=policy.profile(ModelRole.SYNTHESIS)
 assert profile.model=='deepseek-v4-flash' and profile.reasoning.value=='none'
 manifest={'scope':__doc__,'case_ids':[c.case_id for c in cases],'selection':'one max-history turn/group, group hash; not selected by outcomes','max_calls':40,'profile':profile.to_dict(),'budget':2600,'identical_payload_reuse':True,'gold_not_sent':True,'not_run':['Conversation Agent routing','business tools','verifier','official answer scoring']}
 (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=2048)
 cache={};records=[]
 for case in cases:
  record={'case_id':case.case_id,'question':case.query,'history':list(case.history),'gold_evidence':[asdict(e) for e in case.evidence],'arms':{}}
  for arm in ['baseline','candidate']:
   packed=inputs[arm][case.case_id]['anchor'];evidence=[]
   for s in packed['selected']:
    doc=docs[s['document_id']];text=doc.content[s['start']:s['end']];eid='E'+hashlib.sha256(json.dumps([doc.document_id,s['start'],s['end'],text]).encode()).hexdigest()[:12]
    evidence.append({'evidence_id':eid,'text':text,'title':doc.title,'source':{'source_id':doc.document_id,'start_char':s['start'],'end_char':s['end']}})
   claims=[AllowedClaim('knowledge','KNOWLEDGE_FACT',{'status':'OK','evidence':evidence},())]
   payload={'current_message':case.query,'conversation_context':{'recent_messages':[{'role':'user' if i%2==0 else 'assistant','content':t} for i,t in enumerate(case.history)]},'allowed_claims':[asdict(c) for c in claims],'work_item_outcomes':[],'missing_requirement_ids':[],'partial_delivery_allowed':False}
   key=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
   if key in cache:out={**cache[key],'reused':True,'calls':[]}
   else:
    capture=FrameworkCapture(limit=1);provider=AnthropicConversationPlanningProvider({ModelRole.SYNTHESIS:model},model_profile=profile,synthesis_profile=profile,max_tokens=2048,callbacks=(capture,));out={'error':None,'answer':'','reused':False}
    try:
     raw=await provider.compose(payload);answer,used=render_composition(raw,claims);out.update(raw=raw,answer=answer,used_claim_ids=used)
    except Exception as exc:out.update(error=type(exc).__name__,detail=str(exc)[:200])
    out['calls']=clean(capture.calls);cache[key]=out
   record['arms'][arm]={**out,'packed_complete':packed['complete'],'evidence':evidence}
  records.append(record)
  with (args.output/'cases.jsonl').open('a') as f:f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
  print(case.case_id,[(a,r['packed_complete'],r['error'],r['reused']) for a,r in record['arms'].items()],flush=True)
 summary={'cases':len(records),'api_calls':sum(len(r['calls']) for c in records for r in c['arms'].values()),'arms':{a:{'packed_complete':sum(c['arms'][a]['packed_complete'] for c in records),'protocol_errors':sum(c['arms'][a]['error'] is not None for c in records),'drafts_generated':sum(c['arms'][a]['error'] is None for c in records)} for a in ['baseline','candidate']},'answer_correctness':'not scored; structured/cited draft is not correctness proof'}
 (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(args.output/'cases.jsonl.gz').write_bytes(gzip.compress((args.output/'cases.jsonl').read_bytes(),mtime=0));print(json.dumps(summary),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--stages',type=Path,required=True);p.add_argument('--output',type=Path,required=True);asyncio.run(run(p.parse_args()))
