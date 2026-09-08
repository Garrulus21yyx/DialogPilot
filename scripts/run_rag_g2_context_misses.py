"""Production runtime preparation from persisted history; no hand-built TargetTurnContext."""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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



def load_context_cases(path):
    rows = json.loads(Path(path).read_text())
    ids = set()
    cases, roles = [], {}
    for row in rows:
        cid = row['case_id']
        if cid in ids or not row['query'].strip():
            raise ValueError('duplicate ID or empty query')
        ids.add(cid)
        history = row['history']
        if any(m['role'] not in ('user', 'assistant') or not m['text'].strip() for m in history):
            raise ValueError('invalid history')
        cases.append(SimpleNamespace(case_id=cid, history=tuple(m['text'] for m in history), query=row['query']))
        roles[cid] = [m['role'] for m in history]
    if not cases:
        raise ValueError('empty cases')
    return cases, roles


async def run(*, ecommerce=False, cases_path=None, output=None, call_limit=6):
    root=Path('artifacts/eval')
    out=Path(output) if output else root/('rag-g2-context-ecommerce2-2026-09-07' if ecommerce else 'rag-g2-context-miss2-2026-09-07')
    out.mkdir(exist_ok=False)
    if cases_path:
        cases, roles = load_context_cases(cases_path)
    elif ecommerce:
        cases=[
            SimpleNamespace(case_id='synthetic-g2-opened-nonquality',history=('我买的耳机已经拆封了，想了解能不能无理由退货。','你是因为质量问题想退货吗？'),query='不是。'),
            SimpleNamespace(case_id='synthetic-g2-hypothetical-arrival',history=('我只是想了解一般退款流程，不要查询我的订单。','好的，你想了解哪个环节？'),query='如果退款申请审核通过了，就代表已经到账了吗？'),
        ]
        roles={c.case_id:['user','assistant'] for c in cases}
    else:
        snapshot=json.loads(gzip.decompress((root/'rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
        with tempfile.TemporaryDirectory() as folder:
            for name,content in snapshot.items():Path(folder,name).write_text(content)
            ds=RagDataset.load(Path(folder),verify_checksum=True)
        lookup={c.case_id:c for c in ds.select_cases('dev')}
        saved=[json.loads(line) for line in gzip.decompress((root/'rag-g3-flash-pair12-2026-09-07/cases.jsonl.gz').read_bytes()).splitlines()]
        selected=[r['case_id'] for r in saved if r['arm']=='baseline' and not r['candidate_complete']]
        assert len(selected)==2
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
            capture=FrameworkCapture(limit=call_limit)
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
            (out/'manifest.json').write_text(json.dumps({'scope':'build_target_runtime + PostgreSQL history + production memory projection/context loader + manager.prepare; excludes HTTP admission, execution, answer and publication','database':database,'case_count':len(rows),'api_calls':len(capture.calls),'profile':flash.to_dict(),'context_not_handbuilt':True,'cases_sha256':hashlib.sha256(Path(cases_path).read_bytes()).hexdigest() if cases_path else None,'history_origin':'explicit_fixture' if cases_path else ('authored_synthetic' if ecommerce else 'official_doc2dial_speaker_roles'),'cold_projection':True,'synthetic_ecommerce':all(row.get('synthetic',False) for row in json.loads(Path(cases_path).read_text())) if cases_path else ecommerce},indent=2)+'\n')
        finally:
            if components:await components.checkpoint_owner.__aexit__(None,None,None)
            if memory:await memory.close()
            if tools:await tools._client.close()
            pool.close();process.terminate();process.wait(timeout=5)
            with psycopg.connect(admin,autocommit=True) as conn:
                conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(database,))
                conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
    (out/'queries.jsonl.gz').write_bytes(gzip.compress((out/'queries.jsonl').read_bytes(),mtime=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ecommerce',action='store_true')
    parser.add_argument('--cases');parser.add_argument('--output');parser.add_argument('--call-limit',type=int,default=6)
    args=parser.parse_args()
    asyncio.run(run(ecommerce=args.ecommerce,cases_path=args.cases,output=args.output,call_limit=args.call_limit))
