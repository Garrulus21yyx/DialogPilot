"""Isolated query-writing capability; deliberately not Agent routing evaluation."""
import asyncio,json,os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage,SystemMessage,AIMessage
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model,invoke_model
from evaluation.framework_capture import FrameworkCapture
from scripts.replay_rag_rank_selection import read,digest
from scripts.run_rag_selected_composition_pair import clean
OUT=Path('artifacts/eval/rag-pure-query100-2026-09-08')
from scripts.run_rag_pure_query20 import SYSTEM
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.doc2dial_history_roles import history_roles
from scripts.run_rag_fresh100 import ROOT
async def main():
    assert not (OUT/'captures.jsonl').exists()
    ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True)
    roles=history_roles('/tmp/doc2dial_v1.0.1.zip',ds.cases,member='doc2dial_dial_test.json')
    inputs=[{'id':c.case_id,'original_history':list(c.history),'roles':roles[c.case_id],'raw_query':c.query} for c in ds.cases]
    cached=read('artifacts/eval/rag-pure-query20-2026-09-08/captures.jsonl',True)
    oldmanifest=read('artifacts/eval/rag-pure-query20-2026-09-08/manifest.json')
    assert oldmanifest['system']==SYSTEM
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ);policy=ModelPolicy.from_env(values)
    profile=ModelProfile(model='deepseek-v4-flash',provider='deepseek',reasoning=ReasoningEffort.NONE)
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=512)
    (OUT/'manifest.json').write_text(json.dumps({'ids':[r['id'] for r in inputs],'system':SYSTEM,'profile':profile.to_dict(),'max_tokens':512,'api_budget':80,'reused_calls':20,'source_sha256':digest(ROOT/'doc2dial/cases.jsonl'),'mode':'full original history, isolated query generation; not production context loader or Agent','adoption':'development only; no prompt changes after scores'},indent=2)+'\n')
    for i,r in enumerate(inputs):
        messages=[SystemMessage(SYSTEM)]+[(HumanMessage if role=='user' else AIMessage)(text) for role,text in zip(r['roles'],r['original_history'],strict=True)]+[HumanMessage('CURRENT USER MESSAGE:\n'+r['raw_query'])]
        if i<20:
            row=cached[i];assert row['id']==r['id'] and row['raw_query']==r['raw_query']
            request=row['calls'][0]['request'];assert request['system']==SYSTEM
            assert request['messages']==[{'role':m.type.replace('human','user').replace('ai','assistant'),'content':m.content} for m in messages[1:]]
            with (OUT/'captures.jsonl').open('a') as f:f.write(json.dumps({**row,'reused':True},ensure_ascii=False)+'\n')
            continue
        capture=FrameworkCapture(limit=1);row={'id':r['id'],'raw_query':r['raw_query'],'queries':[]}
        try:
            output=await invoke_model(model.ainvoke(messages,config={'callbacks':[capture]}),stage='isolated_query')
            if output.tool_calls or output.invalid_tool_calls or output.response_metadata.get('stop_reason')=='max_tokens' or not output.text.strip():raise ValueError('invalid_query_output')
            row['queries']=[output.text.strip()]
        except Exception as e:row['error_type']=type(e).__name__
        row['calls']=clean(capture.calls)
        with (OUT/'captures.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(row['id'],row['queries'],flush=True)
if __name__=='__main__':asyncio.run(main())
