"""Production runtime preparation from persisted history; no hand-built TargetTurnContext."""
import asyncio
import gzip
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql
from dotenv import dotenv_values
from application.conversation_store import ConversationScope, TurnRole, TurnToAppend
from application.chat_contracts import ChatCommand
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from core.identity import IdentityFactory
from core.model_policy import ModelPolicy, ModelProfile, ModelRole, ReasoningEffort
from evaluation.framework_capture import FrameworkCapture
from evaluation.doc2dial_history_roles import history_roles
from evaluation.rag_pipeline.dataset import RagDataset
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_conversation import PostgresConversationTurnStore
from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.target_runtime_composition import build_target_runtime
from memory.conversation_memory import MemoryManager
from mcp.tool_manager import MCPToolManager
from scripts.run_rag_selected_composition_pair import clean
from scripts import run_rag_resolved_query_pair as replay


async def run():
    root=Path('artifacts/eval');out=root/'rag-context-entry20-v2-2026-09-07';out.mkdir(exist_ok=False)
    ds=RagDataset.load(root/'doc2dial-rag-mini-dev-v1',verify_checksum=True)
    lookup={c.case_id:c for c in ds.select_cases('dev')}
    selected=json.loads((root/'rag-composition20-selected-2026-09-07/manifest.json').read_text())['case_ids']
    cases=[lookup[cid] for cid in selected];roles=history_roles('/tmp/doc2dial_v1.0.1.zip',cases)
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    flash=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    policy=replace(policy,profiles={role:flash for role in ModelRole})
    config=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
    env=dict(item.split('=',1) for item in config['Config']['Env'] if '=' in item)
    admin='postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres'
    database='rag_context_'+uuid.uuid4().hex[:10];url=admin.rsplit('/',1)[0]+'/'+database
    with psycopg.connect(admin,autocommit=True) as conn:conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    PostgresMigrationRunner(url).upgrade();pool=PostgresPool(PostgresPoolConfig(url));pool.open()
    rows=[]
    with tempfile.TemporaryDirectory(prefix='rag-context-redis-') as temp:
        socket=Path(temp)/'redis.sock';process=subprocess.Popen(['redis-server','--port','0','--unixsocket',str(socket),'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        memory=components=tools=None
        try:
            for _ in range(100):
                if socket.exists():break
                await asyncio.sleep(.05)
            memory=MemoryManager(redis_url='unix://'+str(socket),fact_store=PostgresMemoryFactStore(pool),api_key=values['ANTHROPIC_API_KEY'],base_url=policy.base_url,model_profile=flash)
            tools=MCPToolManager(api_key=values['ANTHROPIC_API_KEY'],base_url=policy.base_url,model=flash.model)
            components=await build_target_runtime(database_url=url,postgres_pool=pool,tool_manager=tools,memory=memory,response_delivery=PostgresResponseDeliveryService(pool,resume_binding_secret=uuid.uuid4().hex),model_policy=policy,provider_config={'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},project_root=Path.cwd(),registry=build_default_capability_registry('rag-context-eval'))
            capture=FrameworkCapture(limit=20)
            components.understanding._planner._provider._callbacks=(capture,)
            manager=components.application._turn_runtime._manager
            store=PostgresConversationTurnStore(pool)
            for c in cases:
                conv='context-'+c.case_id;scope=ConversationScope('rag-context-eval','eval-user',conv)
                for i,(role,text) in enumerate(zip(roles[c.case_id],c.history)):
                    tid=f'{conv}-history-{i}'
                    store.append_turn(scope,TurnToAppend(tid,tid,TurnRole.INBOUND if role=='user' else TurnRole.ASSISTANT,text,datetime.now(timezone.utc).isoformat()))
                identity=IdentityFactory().create_invocation(tenant_id=scope.tenant_id,user_id=scope.user_id,conversation_id=conv,request_id='current-'+uuid.uuid4().hex)
                command=ChatCommand(message=c.query,user_id=scope.user_id,tenant_id=scope.tenant_id,conv_id=conv,request_id=str(identity.request_id),authorization_fingerprint='isolated-context-eval')
                components.application._admission.admit(command,identity,bundle_version=components.registry.bundle_version)
                before=len(capture.calls)
                prepared=await manager.prepare(identity,TurnObservations(c.query,()))
                calls=clean(capture.calls[before:])
                queries=[json.loads(a.value_json) for item in (prepared.plan.work.items if prepared.plan.work else ()) if item.allowed_tools==('knowledge_search',) for a in item.arguments if a.name=='query']
                row={'case_id':c.case_id,'raw_query':c.query,'history':list(c.history),'source_roles':roles[c.case_id],'context':asdict(prepared.context),'plan':asdict(prepared.plan),'resolved_queries':queries,'calls':calls}
                rows.append(row)
                with (out/'queries.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=lambda v:v.value)+'\n')
                print(c.case_id,prepared.context.projection_status.value,'visible',len(prepared.context.recent_messages),'/',len(c.history),'queries',queries,flush=True)
                if calls and calls[-1].get('error_type'):raise RuntimeError('provider failure: stop batch')
            (out/'manifest.json').write_text(json.dumps({'scope':'build_target_runtime + PostgreSQL history + production memory projection/context loader + manager.prepare; excludes HTTP admission, execution, answer and publication','database':database,'case_count':len(rows),'api_calls':len(capture.calls),'profile':flash.to_dict(),'history_not_handbuilt':True,'cold_projection':True},indent=2)+'\n')
        finally:
            if components:await components.checkpoint_owner.__aexit__(None,None,None)
            if memory:await memory.close()
            if tools:await tools._client.close()
            pool.close();process.terminate();process.wait(timeout=5)
    replay.OUT=out;replay.evaluate(ds,cases,rows)
    for name in ('queries','retrieval'):(out/(name+'.jsonl.gz')).write_bytes(gzip.compress((out/(name+'.jsonl')).read_bytes(),mtime=0))


if __name__=='__main__':asyncio.run(run())
