"""Real Conversation Agent over retained full WixQA PG corpus; bounded Flash pair."""
import argparse,asyncio,gzip,hashlib,json,os,subprocess
from pathlib import Path
from types import SimpleNamespace
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
 parser=argparse.ArgumentParser();parser.add_argument('--weight',type=float,choices=(.25,.5),required=True);args=parser.parse_args()
 root=Path('artifacts/eval/wixqa-real-entry-pair2-2026-09-08')/str(args.weight);root.mkdir(parents=True,exist_ok=False)
 rows=[json.loads(l) for l in Path('artifacts/eval/wixqa-fixed-dev20-2026-09-08/cases.jsonl').read_text().splitlines()][:2]
 cases=[{'id':f"wix-{args.weight}-{i}",'history':[],'message':r['case']['query'],'required':[]} for i,r in enumerate(rows)]
 (root/'selection.json').write_text(json.dumps({'selection':'first two frozen dev cases, no outcome-based selection','cases':[r['case'] for r in rows]},indent=2)+'\n')
 values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
 values.update(MODEL_PROVIDER='deepseek',RAG_VECTOR_WEIGHT=str(args.weight),RAG_LEXICAL_WEIGHT=str(1-args.weight))
 for role in ModelRole:
  values['MODEL_'+role.value.upper()]='deepseek-v4-flash';values['MODEL_'+role.value.upper()+'_REASONING']='none';values['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
 policy=ModelPolicy.from_env(values);options={'api_key':values['ANTHROPIC_API_KEY'],'max_retries':0,'timeout':60}
 if policy.base_url:options['base_url']=policy.base_url
 c=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
 url='postgresql://'+quote(e.get('POSTGRES_USER','postgres'),safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/dialogpilot_wixqa_eval_20260908'
 model=Path('/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181')
 embedding=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(model,model.name,hashlib.file_digest((model/'pytorch_model.bin').open('rb'),'sha256').hexdigest(),device='cuda',batch_size=16))
 platform=PostgresPool(PostgresPoolConfig(url,min_size=1,max_size=3));retrieval=RetrievalPostgresPool(RetrievalPoolConfig(url,min_size=1,max_size=3));platform.open();retrieval.open();source=None
 try:
  store=PostgresKnowledgeStore(platform,tenant_id='wixqa-eval',locale='en',chunk_strategy='fixed_tokens',embedding_provider=embedding)
  generation=store.active_generation();assert store.collection_scope(generation)==('en','') and store.doc_count()==11167
  source=RecordedKnowledgeSource(backend=PostgresHybridBackend(retrieval),generations=PostgresRetrievalGenerationRegistry(platform),pool=retrieval,embed_query=store.embed_query)
  async with AsyncAnthropic(**options) as transport:
   client=CaptureClient(transport,limit=12);reranker=ToolManagerRerankerAdapter(SimpleNamespace(_result_reranker=ResultReranker(client,policy.profile(ModelRole.RERANK))))
   import api.main as api
   api._knowledge_store=store;api._postgres_pool=platform
   api._knowledge_retriever=KnowledgeRetriever(candidate_source=source,transformer=QueryTransformer(client,policy.profile(ModelRole.REWRITE)),reranker=reranker,evidence_validator=PostgresKnowledgeEvidenceValidator(source))
   await run_full_chain(database_url=url,platform=platform,store=store,client=client,policy=policy,provider_config=options,output=root,handler=api._knowledge_tool_handler,retrieval_policy=rag_retrieval_policy_from_env(values),reranker_version=reranker.version,case_definitions=cases,tenant_id='wixqa-eval',scope_label='WixQA public frozen dev questions, full 6221-article corpus')
 finally:
  if source:(root/'source-route-captures.json.gz').write_bytes(gzip.compress(json.dumps(source.records,ensure_ascii=False,default=str).encode(),mtime=0))
  retrieval.close();platform.close()

if __name__=='__main__':asyncio.run(main())
