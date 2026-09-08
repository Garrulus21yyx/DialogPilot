"""Real multi-turn Agent + full Cloud reference index, not a PG retrieval test."""
import asyncio,json,gzip,os,subprocess,hashlib,argparse
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from urllib.parse import quote
import psycopg
from psycopg import sql
from dotenv import dotenv_values
from anthropic import AsyncAnthropic
from FlagEmbedding import BGEM3FlagModel
from core.model_policy import ModelPolicy,ModelRole
from core.rag_policy import rag_retrieval_policy_from_env
from infrastructure.postgres import PostgresPool,PostgresPoolConfig,PostgresMigrationRunner
from application.default_capability_registry import build_default_capability_registry
from application.knowledge_retriever import KnowledgeRetriever
from infrastructure.knowledge_retriever_adapters import ToolManagerRerankerAdapter
from mcp.result_reranker import ResultReranker
from mcp.query_transformer import QueryTransformer
from scripts.run_rag_tool_calibration import CaptureClient
from evaluation.rag_full_chain_probe import run_full_chain
from evaluation.mtrag_live_source import MtragLiveSource,sha

async def main():
    os.environ['TARGET_ENCODER_ENABLED']='false'
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('artifacts/eval/rag-final-mtrag2-2026-09-08'));root=parser.parse_args().output;root.mkdir(exist_ok=False)
    raw=Path('/tmp/dialogpilot-rag-external-lock-20260907/reference.jsonl')
    refs={r['task_id']:r for r in map(json.loads,raw.read_text().splitlines())}
    selected=[];seen=set()
    for c in map(json.loads,Path('/tmp/dialogpilot-mtrag-adapted-v2-20260907/cases.jsonl').read_text().splitlines()):
        if c['split']=='dev' and 'cloud' in c['query_types'] and int(c['id'].split('<::>')[-1])>=3 and c['group_id'] not in seen:
            selected.append(c);seen.add(c['group_id'])
        if len(selected)==2:break
    cases=[]
    for i,c in enumerate(selected):
        messages=refs[c['id']]['input'];assert messages[-1]['speaker']=='user'
        cases.append({'id':f'mtrag-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}-{i}','history':[('user' if m['speaker']=='user' else 'assistant',m['text']) for m in messages[:-1]],'message':messages[-1]['text'],'required':[]})
    (root/'selection.json').write_text(json.dumps({'selection':'first two Cloud dev tasks with turn>=3 from distinct groups; no outcome selection','reference_sha256':sha(raw),'cases':selected,'targets':[refs[c['id']]['targets'] for c in selected],'grading':'weak-understanding: messages lacking intent classification and known entities; query improvement: Discovery tools/query API and iterative improvement. Original history is untrusted; preserve uncertainty where product identity is ambiguous.','budget':8,'backend_scope':'full Cloud collection 72439 passages, cached exact vectors and BM25, not PG/ANN'},ensure_ascii=False,indent=2)+'\n')
    model_path=Path('/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181')
    identity=json.loads(Path('/tmp/dialogpilot-mtrag-dense-full-20260907/identity.json').read_text())
    for name,digest in identity['model_files'].items():assert sha(model_path/name)==digest
    embedding=BGEM3FlagModel(str(model_path),use_fp16=True,devices='cuda:0')
    source=MtragLiveSource(embedding)
    print('FULL COLLECTION READY',len(source.docs),flush=True)
    c=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
    base='postgresql://'+quote(e.get('POSTGRES_USER','postgres'),safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'
    dbname='dialogpilot_mtrag_final_'+hashlib.sha256(str(root).encode()).hexdigest()[:8]
    with psycopg.connect(base+'postgres',autocommit=True) as conn:
        if conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(dbname,)).fetchone():raise ValueError('final evaluation DB already exists; inspect before reuse')
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
    url=base+dbname;PostgresMigrationRunner(url).upgrade()
    pool=PostgresPool(PostgresPoolConfig(url,min_size=1,max_size=3));pool.open()
    values={**dotenv_values('.env'),**os.environ};values.update(MODEL_PROVIDER='deepseek',RAG_VECTOR_WEIGHT='0.5',RAG_LEXICAL_WEIGHT='0.5')
    for role in ModelRole:
        values['MODEL_'+role.value.upper()]='deepseek-v4-flash';values['MODEL_'+role.value.upper()+'_REASONING']='none';values['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
    policy=ModelPolicy.from_env(values);options={'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url,'max_retries':0,'timeout':60}
    registry=build_default_capability_registry('mtrag-real-eval');registry=replace(registry,bundle_version='mtrag-cloud-public-eval-v1',agents=tuple(replace(a,description='Explain IBM Cloud product documentation, including monitoring, analytics, Watson Assistant and Discovery. Use conversation context for follow-up questions. Provide knowledge guidance; do not execute cloud account changes.') if a.agent_id=='general' else a for a in registry.agents))
    try:
        async with AsyncAnthropic(**options) as transport:
            client=CaptureClient(transport,limit=8);reranker=ToolManagerRerankerAdapter(SimpleNamespace(_result_reranker=ResultReranker(client,policy.profile(ModelRole.RERANK))))
            import api.main as api
            api._knowledge_store=source;api._postgres_pool=pool
            api._knowledge_retriever=KnowledgeRetriever(candidate_source=source,transformer=QueryTransformer(client,policy.profile(ModelRole.REWRITE)),reranker=reranker,evidence_validator=source)
            await run_full_chain(database_url=url,platform=pool,store=source,client=client,policy=policy,provider_config=options,output=root,handler=api._knowledge_tool_handler,retrieval_policy=rag_retrieval_policy_from_env(values),case_definitions=cases,tenant_id='mtrag-real-eval',scope_label='MTRAG two Cloud dev multi-turn questions, full collection 72439 passages',registry=registry,reranker_version=reranker.version,retrieval_backend_label='local exact BGE+BM25 reference backend (not PostgreSQL/ANN)')
    finally:
        (root/'source-route-captures.json.gz').write_bytes(gzip.compress(json.dumps(source.records,ensure_ascii=False).encode(),mtime=0));pool.close()
if __name__=='__main__':asyncio.run(main())
