"""Three synthetic ecommerce cases through the production durable runtime factory."""
import asyncio
import gzip
import hashlib
import json
import subprocess
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from application.chat_contracts import ChatCommand, Accepted
from application.conversation_store import ConversationScope, TurnRole, TurnToAppend
from application.default_capability_registry import build_default_capability_registry
from core.model_policy import ModelRole
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from evaluation.rag_mixed_business import RecordedTools
from infrastructure.postgres_conversation import PostgresConversationTurnStore
from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.target_runtime_composition import build_target_runtime
from memory.conversation_memory import MemoryManager
from mcp.tool_manager import Tool
from services.answer_verifier import AnswerVerifier
from scripts.run_rag_selected_composition_pair import clean

CASES = [
    {'id':'opened-negation','history':[('user','我买的耳机已经拆封了，想退货。'),('assistant','是因为商品质量问题要退吗？')], 'message':'不是。那还能七天无理由退吗？', 'required':['已拆封','非质量原因','不适用无理由退货']},
    {'id':'ellipsis-shipping','history':[('user','我确认是商品质量问题，要寄回退货。'),('assistant','你想了解寄回运费怎么报销吗？')], 'message':'对，加急的也报销吗？', 'required':['标准运费','加急运费不在报销范围']},
    {'id':'historical-policy','history':[('user','我想查质量退货的标准寄回运费报销政策。'),('assistant','需要查哪个时间适用的政策？')], 'message':'2026年3月1日适用的，最多报销多少？请按当时规则，不要用现在的。', 'required':['十二元','2026年3月1日']},
]


async def run_full_chain(*,database_url,platform,store,client,policy,provider_config,output,handler):
    registry=build_default_capability_registry('rag-tool-dev')
    tools=RecordedTools(api_key=provider_config['api_key'],base_url=policy.base_url,model=policy.profile(ModelRole.INTENT).model)
    tools.captures=[];tools.register(Tool(name='knowledge_search',description='检索有效知识原文；query须为完整问题，保留否定、日期及已知条件。',handler=handler,schema={'type':'object','properties':{'query':{'type':'string'},'as_of':{'type':'string'}},'required':['query']},authority='knowledge.active_source',read_only=True))
    capture=FrameworkCapture(limit=client.limit,calls=client.calls)
    verifier=AnswerVerifier(framework_model(policy.profile(ModelRole.VERIFIER),provider_config,max_tokens=4096),model_profile=policy.profile(ModelRole.VERIFIER),callbacks=(capture,))
    generation=store.active_generation()
    def knowledge_context():
        return {'cache_scope':registry.bundle_version,'bundle_version':registry.bundle_version,'pinned_execution_refs':{'bundle_version':registry.bundle_version,'knowledge_backend_ref':generation.backend_fingerprint,'corpus_manifest_ref':generation.manifest_hash,'retrieval_policy_ref':registry.bundle_version,'knowledge_generation_ref':generation.generation_id}}
    manifest={'scope':'synthetic ecommerce; production runtime/admission/coordinator/context/knowledge handler/PostgreSQL retrieval/LLM rerank/compose/verifier/publication; excludes HTTP authentication, external delivery, business writes','cases':CASES,'source_sha256':{f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in ('application/composition_output.py','application/response_assembly.py','infrastructure/target_conversation_provider.py','evaluation/rag_full_chain_probe.py')},'max_api_calls':client.limit}
    (output/'full-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='rag-full-redis-') as temp:
        socket=Path(temp)/'redis.sock';process=subprocess.Popen(['redis-server','--port','0','--unixsocket',str(socket),'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        memory=components=None
        try:
            for _ in range(100):
                if socket.exists():break
                await asyncio.sleep(.05)
            memory=MemoryManager(redis_url='unix://'+str(socket),fact_store=PostgresMemoryFactStore(platform),api_key=provider_config['api_key'],base_url=policy.base_url,model_profile=policy.profile(ModelRole.SYNTHESIS))
            components=await build_target_runtime(database_url=database_url,postgres_pool=platform,tool_manager=tools,memory=memory,response_delivery=PostgresResponseDeliveryService(platform,resume_binding_secret=uuid.uuid4().hex),model_policy=policy,provider_config=provider_config,project_root=Path.cwd(),registry=registry,knowledge_context_factory=knowledge_context,knowledge_verifier=verifier,knowledge_source_validator=store.validate_publication_evidence)
            components.understanding._planner._provider._callbacks=(capture,)
            turns=PostgresConversationTurnStore(platform)
            for case in CASES:
                conv='full-'+case['id'];scope=ConversationScope('rag-tool-dev','eval-user',conv)
                for i,(role,text) in enumerate(case['history']):
                    tid=f'{conv}-{i}';turns.append_turn(scope,TurnToAppend(tid,tid,TurnRole.INBOUND if role=='user' else TurnRole.ASSISTANT,text,datetime.now(timezone.utc).isoformat()))
                before,len_tools=len(client.calls),len(tools.captures)
                try:
                    outcome=await components.coordinator.handle(ChatCommand(message=case['message'],tenant_id='rag-tool-dev',user_id='eval-user',conv_id=conv,request_id='req-'+uuid.uuid4().hex,authorization_fingerprint='isolated-full-rag'))
                    if isinstance(outcome,Accepted):
                        await components.coordinator.pump_once()
                        outcome=await components.coordinator.await_outcome(outcome,timeout_seconds=180)
                    row={'case_id':case['id'],'outcome_type':type(outcome).__name__,'outcome':asdict(outcome)}
                except Exception as exc:row={'case_id':case['id'],'error_type':type(exc).__name__}
                row.update(tools=clean(tools.captures[len_tools:]),api_calls=clean(client.calls[before:]))
                rows.append(row)
                with (output/'full-cases.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
                print('FULL',case['id'],row.get('outcome_type',row.get('error_type')),[t['name'] for t in row['tools']],flush=True)
            (output/'full-report.json').write_text(json.dumps({'cases':len(rows),'api_calls':len(client.calls),'completed':sum(r.get('outcome_type')=='Completed' for r in rows),'answer_quality':'not yet independently scored'},indent=2)+'\n')
            (output/'full-cases.jsonl.gz').write_bytes(gzip.compress((output/'full-cases.jsonl').read_bytes(),mtime=0))
        finally:
            if components:await components.checkpoint_owner.__aexit__(None,None,None)
            if memory:await memory.close()
            await tools._client.close();process.terminate();process.wait(timeout=5)
