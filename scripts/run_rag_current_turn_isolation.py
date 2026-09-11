"""Four diagnostic projections of the SAME failing input; no production adoption."""
from infrastructure.target_model_context import planning_payload_from_request
import asyncio,copy,gzip,json,os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage,AIMessage,SystemMessage
from core.framework_models import framework_model
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from application.conversation_agent import planning_output_schema
from core.structured_model import structured_tool
from evaluation.framework_capture import FrameworkCapture
from scripts.run_rag_selected_composition_pair import clean

async def main():
    root=Path('artifacts/eval/rag-current-turn-isolation4-2026-09-08');root.mkdir(exist_ok=False)
    row=json.loads(gzip.open('artifacts/eval/rag-final-mtrag2-v3-2026-09-08/full-cases.jsonl.gz','rt').readline())
    req=row['api_calls'][0]['request'];p=planning_payload_from_request(req);system=req['system']
    current=p['message'];context=copy.deepcopy(p);context.pop('message')
    native=[HumanMessage(json.dumps(context,ensure_ascii=False)),HumanMessage(current)]
    natural=copy.deepcopy(context);history=natural['conversation_context'].pop('recent_messages')
    natural_messages=[HumanMessage(json.dumps(natural,ensure_ascii=False))]+[
        (HumanMessage if h['role']=='user' else AIMessage)(h['content']) for h in history]+[HumanMessage(current)]
    narrow=copy.deepcopy(p);narrow['domain_capabilities']=narrow['domain_capabilities'][:1]
    narrow['supported_goals']=['general_qa'];narrow['goal_descriptions']={'general_qa':p['goal_descriptions']['general_qa']}
    nohist=copy.deepcopy(p);nohist['conversation_context']['recent_messages']=[]
    variants=[('native_current',native,p),('native_history',natural_messages,p),
              ('knowledge_only_schema',[HumanMessage(json.dumps(narrow,ensure_ascii=False,sort_keys=True))],narrow),
              ('no_history',[HumanMessage(json.dumps(nohist,ensure_ascii=False,sort_keys=True))],nohist)]
    (root/'manifest.json').write_text(json.dumps({'gap':'R01','budget':4,'purpose':'diagnosis only; full original baseline system, same current question; schema/history reductions are NOT production changes','variants':[{'id':name,'messages':[m.model_dump(mode='json') for m in messages]} for name,messages,_ in variants]},ensure_ascii=False,indent=2)+'\n')
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    model=framework_model(ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek'),{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url,'max_retries':0},max_tokens=800)
    capture=FrameworkCapture(limit=4)
    for name,messages,payload in variants:
        before=len(capture.calls)
        tool=structured_tool('submit_turn_plan',planning_output_schema(payload['supported_goals'],payload['knowledge_filter_contract']))
        result=await model.with_structured_output(tool,include_raw=True).ainvoke([SystemMessage(system),*messages],config={'callbacks':[capture]})
        record={'id':name,'parsed':result['parsed'],'parsing_error':str(result['parsing_error']) if result['parsing_error'] else None,'calls':clean(capture.calls[before:])}
        with (root/'results.jsonl').open('a') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
        print(name,result['parsed'],flush=True)
    (root/'results.jsonl.gz').write_bytes(gzip.compress((root/'results.jsonl').read_bytes(),mtime=0))
if __name__=='__main__':asyncio.run(main())
