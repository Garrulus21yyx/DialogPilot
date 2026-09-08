"""Original conversation/question -> frozen Flash query generation for two corpora."""
import asyncio,json,os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import SystemMessage,HumanMessage,AIMessage
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model,invoke_model
from evaluation.framework_capture import FrameworkCapture
from scripts.run_rag_pure_query20 import SYSTEM
from scripts.run_rag_selected_composition_pair import clean
from scripts.replay_rag_rank_selection import read,digest
from scripts.run_rag_fresh100 import ROOT
OUT=Path('artifacts/eval/rag-query-other200-2026-09-08')
async def main():
    assert not (OUT/'captures.jsonl').exists()
    sel=read(ROOT/'selection.json');refs={r['task_id']:r for r in read('/tmp/dialogpilot-rag-external-lock-20260907/reference.jsonl',True)};inputs=[]
    for c in sel['MTRAG']:
        source=refs[c['id']];assert 'mtrag-'+source['conversation_id']==c['group_id'];mm=source['input'];assert mm[-1]['speaker']=='user'
        inputs.append({'dataset':'MTRAG','id':c['id'],'messages':[{'role':{'agent':'assistant','user':'user'}[m['speaker']],'content':m['text']} for m in mm]})
    for c in sel['WixQA']:inputs.append({'dataset':'WixQA','id':c['id'],'messages':[{'role':'user','content':c['query']}]})
    (OUT/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ);policy=ModelPolicy.from_env(values);profile=ModelProfile(model='deepseek-v4-flash',provider='deepseek',reasoning=ReasoningEffort.NONE)
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=512)
    (OUT/'manifest.json').write_text(json.dumps({'input_sha256':digest(OUT/'inputs.json'),'selection_sha256':digest(ROOT/'selection.json'),'reference_sha256':digest('/tmp/dialogpilot-rag-external-lock-20260907/reference.jsonl'),'system':SYSTEM,'profile':profile.to_dict(),'max_tokens':512,'calls_budget':200,'scope':'pure query; MTRAG reference.input only; Wix original question only; no gold/targets/rewrite/corpus in prompt'},indent=2)+'\n')
    for i,r in enumerate(inputs):
        mm=r['messages'];messages=[SystemMessage(SYSTEM)]+[(HumanMessage if m['role']=='user' else AIMessage)(m['content']) for m in mm[:-1]]+[HumanMessage('CURRENT USER MESSAGE:\n'+mm[-1]['content'])]
        capture=FrameworkCapture(limit=1);row={'dataset':r['dataset'],'id':r['id'],'queries':[]}
        try:
            out=await invoke_model(model.ainvoke(messages,config={'callbacks':[capture]}),stage='isolated_query')
            if out.tool_calls or out.invalid_tool_calls or out.response_metadata.get('stop_reason')=='max_tokens' or not out.text.strip():raise ValueError('invalid_query_output')
            row['queries']=[out.text.strip()]
        except Exception as e:row['error_type']=type(e).__name__
        row['calls']=clean(capture.calls)
        with (OUT/'captures.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(i,row['dataset'],row['queries'],row.get('error_type',''),flush=True)
if __name__=='__main__':asyncio.run(main())
