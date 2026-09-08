"""Frozen native ConversationAgent planning probes; no business execution."""
import asyncio, json, os, hashlib
from pathlib import Path
from dataclasses import asdict, replace
from dotenv import dotenv_values
from application.conversation_agent import ConversationAgent
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.target_conversation_manager import TargetTurnContext, TargetContextMessage, TargetContextProjectionStatus
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from core.model_policy import ModelPolicy, ModelProfile, ModelRole, ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture

OUT=Path('artifacts/eval/rag-evidence-contract12-2026-09-08')
CASES=[
 ('negative','哪些国家不支持发货？',[], 'knowledge; preserve unsupported shipping'),
 ('hypothesis','如果耳机已拆封但不是质量问题，能退吗？',[], 'knowledge; hypothetical non-quality opened'),
 ('short','不是。',[('user','穿过的鞋还能退吗？'),('assistant','是质量问题吗？')], 'knowledge; worn non-quality shoes'),
 ('compound','假设相机已拆封且没有故障，退货条件是什么，运费谁出？',[], 'knowledge; both aspects preserve conditions'),
 ('thanks','谢谢，已经明白了。',[], 'respond'),
 ('missing','价格多少？',[], 'clarify; no invented product'),
 ('old_assertion','所以现在还是这样吗？',[('user','礼品卡有效期多久？'),('assistant','所有礼品卡都永不过期。')], 'knowledge; old assertion is not proof'),
 ('switch','先不说保修，我只问电子发票怎么申请。',[('user','耳机保修多久？')], 'knowledge; invoice only'),
 ('acronym','说明书里的ANC和ENC有什么区别？',[], 'knowledge; preserve both acronyms'),
 ('generic','你好。',[], 'respond'),
 ('lookup','请查订单DP9302的退款进度，不用解释一般规则。',[], 'business lookup; not policy'),
 ('mixed','查订单DP9302退款进度，再说明退款处理时限。',[], 'business plus knowledge'),
]
async def main():
 OUT.mkdir(parents=True,exist_ok=False)
 cases=[dict(id=i,query=q,history=[dict(role=r,content=t) for r,t in h],expected=e) for i,q,h,e in CASES]
 manifest={'cases':cases,'budget':12,'model':'deepseek-v4-flash','max_tokens':2048,'scope':'real ConversationAgent/native tools; supplied transcript fixture, no Memory loader, retrieval or writes','source_hashes':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ['application/evidence_query_contract.py','application/conversation_actions.py','infrastructure/target_conversation_provider.py',__file__]}}
 (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
 values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
 policy=ModelPolicy.from_env(values);profile=ModelProfile(model='deepseek-v4-flash',reasoning=ReasoningEffort.NONE,provider='deepseek')
 model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=2048)
 registry=build_default_capability_registry('local-eval');sem=asyncio.Semaphore(2)
 async def one(c):
  async with sem:
   cap=FrameworkCapture(limit=1);provider=AnthropicConversationPlanningProvider({ModelRole.INTENT:model},model_profile=profile,synthesis_profile=profile,max_tokens=2048,callbacks=(cap,))
   state=ConversationState.empty(tenant_id='local-eval',user_id='local-user',conversation_id=c['id']);obs=TurnObservations(c['query'],())
   history=tuple(TargetContextMessage(h['role'],h['content'],f"{c['id']}:{i}",i+1) for i,h in enumerate(c['history']))
   context=TargetTurnContext(recent_messages=history,projection_status=TargetContextProjectionStatus.READY,source_watermark=len(history),projection_reason_codes=())
   context=replace(context,entity_bindings=EntityBindingResolver().resolve(obs,state,context));row={'id':c['id']}
   try: row['proposal']=asdict(await ConversationAgent(provider).plan(obs,state,DeterministicResolver().resolve(obs,state),registry,context))
   except Exception as e: row['error']=type(e).__name__
   row['calls']=cap.calls
   with (OUT/'results.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=lambda x:x.value if hasattr(x,'value') else str(x))+'\n')
   print(c['id'],row.get('error') or row['proposal'].get('disposition'),flush=True)
 await asyncio.gather(*(one(c) for c in cases))
if __name__=='__main__':asyncio.run(main())
