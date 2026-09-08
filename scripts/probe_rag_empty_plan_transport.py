"""One bounded replay of a frozen planning request, with response-body evidence."""
import asyncio,gzip,json,os,hashlib
from pathlib import Path
import httpx2 as httpx
from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage,SystemMessage
from jsonschema import Draft202012Validator
from core.framework_models import framework_model
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from scripts.run_rag_selected_composition_pair import clean

async def main():
    root=Path('artifacts/eval/rag-empty-plan-transport-2026-09-08');root.mkdir(exist_ok=True)
    if (root/'report.json').exists():raise ValueError('replay already recorded')
    src=Path('artifacts/eval/rag-entry-multicondition4-2026-09-08/run/full-cases.jsonl.gz')
    row=json.loads(gzip.decompress(src.read_bytes()).splitlines()[-1]);request=row['api_calls'][0]['request']
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    count=0;wire=[]
    async def before(req):
        nonlocal count
        count+=1
        if count>1:raise RuntimeError('single_request_budget')
    async def after(response):
        await response.aread()
        data=response.json()
        wire.append(clean({k:data[k] for k in ('content','stop_reason','usage','type') if k in data}))
    async with httpx.AsyncClient(event_hooks={'request':[before],'response':[after]},timeout=60) as http:
        profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
        model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=request['max_tokens'])
        model._async_client=AsyncAnthropic(api_key=values['ANTHROPIC_API_KEY'],base_url=policy.base_url,http_client=http,max_retries=0)
        result=None;error=None
        try:
            result=await model.bind_tools(request['tools'],tool_choice=request['tools'][0]['name']).ainvoke([SystemMessage(content=request['system']),HumanMessage(content=request['messages'][0]['content'])])
        except Exception as exc:error=type(exc).__name__
        valid=False
        if result is not None:
            try:
                assert len(result.tool_calls)==1
                Draft202012Validator(request['tools'][0]['input_schema']).validate(result.tool_calls[0]['args']);valid=True
            except Exception: pass
        report={'scope':__doc__,'http_requests':count,'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'request':request,'response':wire,'sdk_result':clean(result.model_dump(mode='json')) if result else None,'schema_valid':valid,'error_type':error}
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'http_requests':count,'schema_valid':valid,'error_type':error,'wire_inputs':[b.get('input') for w in wire for b in w.get('content',[]) if b.get('type')=='tool_use']},ensure_ascii=False))

if __name__=='__main__':asyncio.run(main())
