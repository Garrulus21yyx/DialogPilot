"""Actual MemoryManager/window loader/ConversationAgent; isolated Redis fixture."""
import asyncio,json,os,gzip,subprocess,tempfile,time
from dataclasses import asdict,replace
from pathlib import Path
from dotenv import dotenv_values
from core.identity import IdentityFactory
from core.model_policy import ModelPolicy,ModelRole,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from memory.conversation_memory import MemoryManager,Message,MsgRole
from application.memory_projection import MemoryProjectionResult,MemoryProjectionState,MemoryRetrievalOutcome
from application.conversation_agent import ConversationAgent
from application.conversation_state import ConversationState
from application.deterministic_resolution import DeterministicResolver,TurnObservations
from application.entity_binding import EntityBindingResolver
from application.default_capability_registry import build_default_capability_registry
from infrastructure.target_turn_context import TargetTurnContextLoader
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from evaluation.framework_capture import FrameworkCapture
from evaluation.doc2dial_history_roles import history_roles
from evaluation.rag_pipeline.dataset import RagDataset
from scripts.run_rag_fresh100 import ROOT
from scripts.replay_rag_rank_selection import read,digest
from scripts.run_rag_selected_composition_pair import clean
from mcp.tool_manager import ToolResult
OUT=Path('artifacts/eval/rag-agent-context20-2026-09-08')
class Projection:
    """Fixture substitutes PG watermarks, not MemoryManager's read policy."""
    def __init__(self,memory):self.memory=memory
    async def get_projection_result(self,t,u,c,*,current_request_id):
        context=await self.memory.get_current_context(u,c)
        high=max((m.seq for m in context.recent_messages),default=0)
        return MemoryProjectionResult(MemoryProjectionState.READY,context,high,{'working_window':high,'thread_summary':high},MemoryRetrievalOutcome.NOT_NEEDED)
class EmptyEpisodes:
    def __init__(self):self.calls=[]
    async def execute_for_agent(self,name,params,**kwargs):
        self.calls.append({'name':name,'params':params})
        assert name=='service_episode_search'
        return ToolResult(True,{'status':'NO_MATCH','hits':[]},name)
async def run(socket):
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
    policy=ModelPolicy.from_env(values);profile=ModelProfile(model='deepseek-v4-flash',provider='deepseek',reasoning=ReasoningEffort.NONE)
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=2048)
    memory=MemoryManager(redis_url='unix://'+socket,api_key=values['ANTHROPIC_API_KEY'],base_url=policy.base_url,model_profile=profile)
    ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True);cases=ds.cases[:20];roles=history_roles('/tmp/doc2dial_v1.0.1.zip',cases,member='doc2dial_dial_test.json')
    registry=build_default_capability_registry('local-eval');registry=replace(registry,bundle_version='doc2dial-public-calibration-v1',agents=tuple(replace(a,description='Explain public government service documentation for DMV, Social Security, Veterans Affairs and Student Aid. Retrieve policy and procedure evidence; do not execute government account actions.') if a.agent_id=='general' else a for a in registry.agents))
    paths=['memory/conversation_memory.py','infrastructure/target_turn_context.py','application/conversation_agent.py','application/conversation_actions.py','infrastructure/target_conversation_provider.py','infrastructure/target_model_context.py']
    (OUT/'manifest.json').write_text(json.dumps({'ids':[c.case_id for c in cases],'profile':profile.to_dict(),'budget':20,'selection':'first20 of frozen100, before model output','sources':{p:digest(p) for p in paths},'fixture':'real isolated Redis + MemoryManager; PG projection watermark adapter fixture; empty service episodes; no fabricated summary/knowledge','query_reference_not_in_model':True},indent=2)+'\n')
    for c in cases:
        for i,text in enumerate(c.history):
            m=Message(MsgRole(roles[c.case_id][i]),text,message_id=f'{c.case_id}:{i}',seq=i+1)
            await memory.project_working_message('local-user',c.case_id,m,event_key=str(i))
            # Same threshold check. Public short histories must not require hidden summary calls.
            if await memory._needs_compression('local-user',c.case_id):raise RuntimeError('summary_budget_requires_preregistered_run')
            await memory.project_thread_summary('local-user',c.case_id,event_key=str(i))
        inv=IdentityFactory().create_invocation(tenant_id='local-eval',user_id='local-user',conversation_id=c.case_id,request_id='eval-current')
        state=ConversationState.empty(tenant_id='local-eval',user_id='local-user',conversation_id=c.case_id);obs=TurnObservations(c.query,());det=DeterministicResolver().resolve(obs,state)
        tools=EmptyEpisodes();context=await TargetTurnContextLoader(Projection(memory),tools).load(inv,obs,state,det)
        context=replace(context,entity_bindings=EntityBindingResolver().resolve(obs,state,context))
        capture=FrameworkCapture(limit=1);provider=AnthropicConversationPlanningProvider({ModelRole.INTENT:model},model_profile=profile,synthesis_profile=profile,max_tokens=2048,callbacks=(capture,))
        row={'id':c.case_id,'raw_query':c.query,'original_history':list(c.history),'roles':roles[c.case_id],'loaded_context':asdict(context),'memory_calls':tools.calls}
        try:
            proposal=await ConversationAgent(provider).plan(obs,state,det,registry,context)
            row['proposal']=asdict(proposal);row['queries']=[json.loads(a.value_json) for cmd in proposal.commands if cmd.tool_id=='knowledge_search' for a in cmd.arguments if a.name=='query']
        except Exception as e:row['error_type']=type(e).__name__;row['queries']=[]
        row['calls']=clean(capture.calls)
        with (OUT/'captures.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=lambda v:v.value)+'\n')
        print(c.case_id,row.get('error_type','OK'),row['queries'],flush=True)
    await memory._redis.aclose();await memory._client.close()
    assert all(digest(p)==h for p,h in read(OUT/'manifest.json')['sources'].items())
if __name__=='__main__':
    assert not (OUT/'captures.jsonl').exists()
    with tempfile.TemporaryDirectory(prefix='rag-redis-') as d:
        socket=d+'/redis.sock';server=subprocess.Popen(['redis-server','--port','0','--unixsocket',socket,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if Path(socket).exists():break
                time.sleep(.02)
            asyncio.run(run(socket))
        finally:server.terminate();server.wait()
