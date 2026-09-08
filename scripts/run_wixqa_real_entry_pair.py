"""Real Conversation Agent over retained full WixQA PG corpus; bounded Flash pair."""
import argparse,asyncio,gzip,hashlib,json,os,subprocess
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from application.default_capability_registry import build_default_capability_registry
from urllib.parse import quote
from dotenv import dotenv_values
from anthropic import AsyncAnthropic
from core.model_policy import ModelPolicy,ModelRole
from core.rag_policy import rag_retrieval_policy_from_env
from infrastructure.postgres import PostgresPool,PostgresPoolConfig
from infrastructure.retrieval_postgres import RetrievalPostgresPool,RetrievalPoolConfig,PostgresRetrievalGenerationRegistry
from infrastructure.bge_m3_embedding import LocalBGEM3EmbeddingProvider,BGEM3EmbeddingConfig
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeEvidenceValidator
from evaluation.recorded_knowledge_source import RecordedKnowledgeSource
from evaluation.rag_full_chain_probe import run_full_chain
from scripts.run_rag_tool_calibration import CaptureClient
from application.knowledge_retriever import KnowledgeRetriever
from mcp.result_reranker import ResultReranker
from mcp.query_transformer import QueryTransformer
from infrastructure.knowledge_retriever_adapters import ToolManagerRerankerAdapter

async def main():
 os.environ['TARGET_ENCODER_ENABLED']='false'  # Chinese ecommerce classifier has no Wix bundle calibration.
 parser=argparse.ArgumentParser();parser.add_argument('--weight',type=float,choices=(.25,.5),required=True);parser.add_argument('--dataset',choices=('wixqa','doc2dial'),default='wixqa');parser.add_argument('--offset',type=int,default=0);parser.add_argument('--output',type=Path,default=Path('artifacts/eval/wixqa-real-entry-scoped-pair2-v2-2026-09-08'));args=parser.parse_args()
 if args.offset not in range(19):raise ValueError('two-case offset outside frozen dev20')
 root=args.output/str(args.weight);root.mkdir(parents=True,exist_ok=False)
 rows=[json.loads(l) for l in Path('artifacts/eval/wixqa-fixed-dev20-2026-09-08/cases.jsonl').read_text().splitlines()][args.offset:args.offset+2]
 cases=[{'id':f"wix-{hashlib.sha256(str(root).encode()).hexdigest()[:12]}-{args.offset+i}",'history':[],'message':r['case']['query'],'required':[]} for i,r in enumerate(rows)]
 if args.dataset=='doc2dial':
  snapshot_path=Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz')
  snapshot=json.loads(gzip.decompress(snapshot_path.read_bytes()))['doc2dial-rag-mini-dev-v1']
  selected=[];groups=set()
  for item in map(json.loads,snapshot['cases.jsonl'].splitlines()):
   if item['split']=='dev' and len(item['history'])>=2 and item['group_id'] not in groups:
    selected.append(item);groups.add(item['group_id'])
   if len(selected)==2:break
  cases=[{'id':f"doc-{hashlib.sha256(str(root).encode()).hexdigest()[:12]}-{i}",'history':[('user' if n%2==0 else 'assistant',v) for n,v in enumerate(c['history'])],'message':c['query'],'required':[]} for i,c in enumerate(selected)]
  rows=[{'case':c} for c in selected]
  (root/'source-snapshot-sha256.txt').write_text(hashlib.sha256(snapshot_path.read_bytes()).hexdigest()+'\n')
 (root/'selection.json').write_text(json.dumps({'selection':'consecutive frozen dev cases, no outcome-based selection','offset':args.offset,'cases':[r['case'] for r in rows]},indent=2)+'\n')
 values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
 values.update(MODEL_PROVIDER='deepseek',RAG_VECTOR_WEIGHT=str(args.weight),RAG_LEXICAL_WEIGHT=str(1-args.weight))
 for role in ModelRole:
  values['MODEL_'+role.value.upper()]='deepseek-v4-flash';values['MODEL_'+role.value.upper()+'_REASONING']='none';values['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
 policy=ModelPolicy.from_env(values);options={'api_key':values['ANTHROPIC_API_KEY'],'max_retries':0,'timeout':60}
 if policy.base_url:options['base_url']=policy.base_url
 c=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
 url='postgresql://'+quote(e.get('POSTGRES_USER','postgres'),safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/dialogpilot_wixqa_eval_20260908'
 if args.dataset=='doc2dial':
  import psycopg
  from psycopg import sql
  from infrastructure.postgres import PostgresMigrationRunner
  dbname='dialogpilot_doc2dial_final_20260908'
  with psycopg.connect(url.rsplit('/',1)[0]+'/postgres',autocommit=True) as conn:
   if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(dbname,)).fetchone():conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
  url=url.rsplit('/',1)[0]+'/'+dbname
  PostgresMigrationRunner(url).upgrade()
 model=Path('/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181')
 embedding=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(model,model.name,hashlib.file_digest((model/'pytorch_model.bin').open('rb'),'sha256').hexdigest(),device='cuda',batch_size=16))
 platform=PostgresPool(PostgresPoolConfig(url,min_size=1,max_size=3));retrieval=RetrievalPostgresPool(RetrievalPoolConfig(url,min_size=1,max_size=3));platform.open();retrieval.open();source=None
 try:
  tenant='wixqa-eval' if args.dataset=='wixqa' else 'doc2dial-real-eval'
  store=PostgresKnowledgeStore(platform,tenant_id=tenant,locale='en',chunk_strategy='fixed_tokens' if args.dataset=='wixqa' else 'structure_aware',embedding_provider=embedding)
  if args.dataset=='doc2dial':
   from mcp.source_document import SourceDocument
   docs=[json.loads(l) for l in snapshot['corpus.jsonl'].splitlines()]
   store.import_documents(tuple(SourceDocument.create(source_id=d['id'],title=d['title'],content=d['content'],source_type='text') for d in docs))
  generation=store.active_generation();assert store.collection_scope(generation)==('en','')
  if args.dataset=='wixqa':assert store.doc_count()==11167
  source=RecordedKnowledgeSource(backend=PostgresHybridBackend(retrieval),generations=PostgresRetrievalGenerationRegistry(platform),pool=retrieval,embed_query=store.embed_query)
  async with AsyncAnthropic(**options) as transport:
   client=CaptureClient(transport,limit=12);reranker=ToolManagerRerankerAdapter(SimpleNamespace(_result_reranker=ResultReranker(client,policy.profile(ModelRole.RERANK))))
   import api.main as api
   api._knowledge_store=store;api._postgres_pool=platform
   api._knowledge_retriever=KnowledgeRetriever(candidate_source=source,transformer=QueryTransformer(client,policy.profile(ModelRole.REWRITE)),reranker=reranker,evidence_validator=PostgresKnowledgeEvidenceValidator(source))
   default_registry=build_default_capability_registry(tenant)
   registry=replace(default_registry,bundle_version=args.dataset+'-public-eval-v1',agents=tuple(replace(agent,description='Resolve Wix customer-support knowledge questions using the available Wix Help Center corpus, including site editing, product setup, billing explanations and general procedures. Provide evidence-based guidance; this knowledge capability does not operate user accounts or execute website changes.' if args.dataset=='wixqa' else 'Explain government service information from the available Doc2Dial corpus: DMV, social security, veterans and student aid procedures. Use the supplied conversation history to understand follow-up questions. Ask for clarification when the user objective is ambiguous; this knowledge capability explains documents and does not execute government transactions.') if agent.agent_id=='general' else agent for agent in default_registry.agents))
   (root/'evaluation-domain.json').write_text(json.dumps({'encoder_enabled':False,'bundle_version':registry.bundle_version,'general_description':registry.agents[0].description,'scope':'Evaluation tenant only; no question-specific instructions or answer injection'},indent=2)+'\n')
   await run_full_chain(database_url=url,platform=platform,store=store,client=client,policy=policy,provider_config=options,output=root,handler=api._knowledge_tool_handler,retrieval_policy=rag_retrieval_policy_from_env(values),reranker_version=reranker.version,case_definitions=cases,tenant_id=tenant,scope_label='WixQA full 6221-article corpus' if args.dataset=='wixqa' else 'Doc2Dial existing 100-document dev corpus, two original multi-turn questions',registry=registry)
 finally:
  if source:(root/'source-route-captures.json.gz').write_bytes(gzip.compress(json.dumps(source.records,ensure_ascii=False,default=str).encode(),mtime=0))
  retrieval.close();platform.close()

if __name__=='__main__':asyncio.run(main())
