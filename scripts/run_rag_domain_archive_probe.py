"""One real domain Agent over frozen long knowledge output; no retrieval or writes."""
import asyncio,gzip,json,os
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from langgraph.store.memory import InMemoryStore
from application.capability_registry import CapabilityEffect,CapabilityRisk
from application.default_capability_registry import build_default_capability_registry
from application.work_item import WorkItem,ControlMode,ArgumentValue
from application.orchestration_runtime import AgentContextView
from infrastructure.target_framework_agent import TargetFrameworkAgent
from core.framework_models import framework_model
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from evaluation.framework_capture import FrameworkCapture
from mcp.tool_manager import MCPToolManager,Tool
from scripts.run_rag_selected_composition_pair import clean
from scripts.adapt_mtrag_retrieval_dataset import digest
ROOT=Path(os.environ.get('RAG_ARCHIVE_PROBE_OUTPUT','artifacts/eval/rag-g4-domain-archive1-2026-09-08'))
async def main():
    src=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
    row=next(r for r in json.load(gzip.open(src,'rt')) if r['case_id']=='e1b602e47ded79a35d8df4eefe194e39<::>1')
    v=row['arms']['0.75'];view=json.loads(v['wire']);query='Is it possible to build a dialog skill in a language other than English?'
    data={'status':'OK','evidence_pack':{'query':view['query_used'],'index_manifest_fingerprint':'frozen-eval-fixture','items':[{'chunk_id':cid,'text':e['text'],'title':e['title'],'source_ref':e['source']} for cid,e in zip(v['packed_ids'],view['evidence'],strict=True)]}}
    ROOT.mkdir(exist_ok=False)
    files=['infrastructure/target_framework_agent.py','infrastructure/target_context_compaction.py','infrastructure/target_result_archive.py','infrastructure/target_domain_outcome.py','infrastructure/target_agent_middleware.py','core/structured_model.py']
    (ROOT/'fixture.json').write_text(json.dumps({'query':query,'source_sha256':digest(src),'data':data,'source_files':{p:digest(Path(p)) for p in files}},indent=2)+'\n')
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values);profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=1200)
    capture=FrameworkCapture(limit=9);tool_calls=[]
    manager=MCPToolManager('fixture-unused-key',model='fixture-unused-model')
    async def lookup(params,context):
        tool_calls.append(params);return data
    manager.register(Tool('knowledge_search','Search the supplied product documentation and return source evidence.',lookup,{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False},authority='knowledge.active_source'))
    item=WorkItem('archive-probe','general',query,ControlMode.DELEGATED,('knowledge_search',),(),(ArgumentValue.create('question',query),),('knowledge.active_source',),(),CapabilityEffect.READ,CapabilityRisk.MEDIUM,'agent-result-v1','customer-service-default:v1',1,'registry:eval',timeout_seconds=60,max_steps=8)
    context=AgentContextView(item,query,(),(),(),2000,{'tenant_id':'tenant-a','user_id':'eval','conversation_id':'archive-probe','request_id':'archive-probe','invocation_key':'archive-probe'})
    agent=TargetFrameworkAgent(model,manager,review_model=model,review_available_tokens=14200,result_store=InMemoryStore(),registry=build_default_capability_registry('tenant-a'),system_prompt='Answer the supplied product documentation question using retrieved evidence. Preserve source conditions and uncertainty.',callbacks=(capture,))
    result=None;error=None
    try:result=await agent(context)
    except Exception as exc:error={'type':type(exc).__name__,'message':str(exc)}
    report={'scope':__doc__,'max_steps':item.max_steps,'call_cap':9,'api_calls':len(capture.calls),'profile':profile.to_dict(),'lookup_calls':tool_calls,'result':asdict(result) if result else None,'error':error}
    (ROOT/'result.json').write_text(json.dumps(report,default=str,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'calls.json.gz').write_bytes(gzip.compress(json.dumps(clean(capture.calls),default=str,ensure_ascii=False).encode(),mtime=0))
    print(json.dumps({'calls':len(capture.calls),'lookups':len(tool_calls),'status':str(result.status) if result else None,'reason':result.reason_code if result else None,'answer':result.candidate_response if result else None,'error':error},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
