"""Fixed development queries through the real knowledge tool, PG and packer.

No planning or answer generation. Provider-free arms run first; optional Flash
listwise uses the same candidate budget and the same frozen queries.
"""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.parse import urlsplit,urlunsplit
import uuid

import psycopg
from psycopg import sql
from dotenv import dotenv_values
from application.cost_budget_policy import OFFLINE_KNOWLEDGE_INGEST_BUDGET
from application.knowledge_retriever import KnowledgeRetriever
from application.knowledge_tool_contract import knowledge_query_schema,knowledge_tool_schema_for_context
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from infrastructure.postgres import PostgresMigrationRunner,PostgresPool,PostgresPoolConfig
from infrastructure.retrieval_postgres import RetrievalPostgresPool,RetrievalPoolConfig
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource,PostgresKnowledgeEvidenceValidator
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.bge_m3_embedding import LocalBGEM3EmbeddingProvider,BGEM3EmbeddingConfig
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from infrastructure.knowledge_retriever_adapters import ToolManagerRerankerAdapter
from mcp.source_document import SourceDocument
from mcp.tool_manager import MCPToolManager,Tool


class NoRewrite:
    async def standalone(self,*args,**kwargs):raise AssertionError('resolved query must bypass rewrite')
    async def expand(self,*args,**kwargs):raise AssertionError('expansion disabled')


class FusionOnly:
    version='evaluation-fusion-only-v1'
    async def rerank(self,query,rows):return tuple(row['chunk_id'] for row in rows),False


class CaptureReranker:
    def __init__(self,inner):self.inner=inner;self.version=inner.version;self.rows=[];self.ordered=();self.fallback=None
    async def rerank(self,query,rows):
        self.rows=[dict(row) for row in rows]
        self.ordered,self.fallback=await self.inner.rerank(query,rows)
        return self.ordered,self.fallback


def covered(case,spans):
    return all(any(doc==gold.document_id and start<=gold.start_char and end>=gold.end_char for doc,start,end in spans) for gold in case.evidence)


async def evaluate(args,url):
    from api import main as api
    ds=RagDataset.load(args.dataset,verify_checksum=True)
    raw=gzip.decompress((args.queries/'queries.jsonl.gz').read_bytes())
    manifest=json.loads((args.queries/'manifest.json').read_text())
    assert hashlib.sha256(raw).hexdigest()==manifest['frozen_queries_sha256']
    queries=[json.loads(line) for line in raw.splitlines()]
    cases={c.case_id:c for c in ds.select_cases('dev')}
    assert len(queries)==20 and len({q['case_id'] for q in queries})==20
    model=args.embedding.resolve()
    with (model/'pytorch_model.bin').open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    embedding=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(model,model.name,digest,device='cuda',batch_size=8))
    local=LocalKnowledgeReranker(args.reranker,device='cuda')
    PostgresMigrationRunner(url).upgrade()
    pool=PostgresPool(PostgresPoolConfig(url,min_size=1,max_size=3));pool.open()
    retrieval=RetrievalPostgresPool(RetrievalPoolConfig(url,min_size=1,max_size=3));retrieval.open()
    tools=MCPToolManager(api_key='unused-local-tool-runtime');client=None
    try:
        store=PostgresKnowledgeStore(pool,tenant_id='rag-pair-dev',embedding_provider=embedding)
        documents=[SourceDocument.create(source_id=d.document_id,title=d.title,content=d.content,source_type=d.metadata.get('source_type','text'),effective_from=datetime(2025,1,1,tzinfo=timezone.utc)) for d in ds.documents]
        batch=OFFLINE_KNOWLEDGE_INGEST_BUDGET.max_sources_per_batch
        for offset in range(0,len(documents),batch):store.import_documents(documents[offset:offset+batch])
        source=PostgresKnowledgeCandidateSource(backend=PostgresHybridBackend(retrieval),generations=store._generations,pool=retrieval,embed_query=store.embed_query)
        api._knowledge_store,api._postgres_pool=store,pool
        handler_capture = {}
        async def observed_handler(params, context):
            handler_capture['data'] = await api._knowledge_tool_handler(params, context)
            return handler_capture['data']
        tools.register(Tool('knowledge_search','检索政策',observed_handler,knowledge_query_schema(),
            schema_factory=knowledge_tool_schema_for_context,schema_factory_version='knowledge-filter-contract-v1',authority='knowledge.active_source'))
        arms=[('fusion25',.25,FusionOnly()),('fusion50',.5,FusionOnly()),('ce25',.25,local),('ce50',.5,local)]
        if args.replay_from:
            saved=[json.loads(line) for line in gzip.decompress((args.replay_from/'cases.jsonl.gz').read_bytes()).splitlines()]
            class FrozenOrder:
                version='evaluation-frozen-order-replay-v1'
                def __init__(self, name):self.by_query={r['query']:r for r in saved if r['arm']==name}
                async def rerank(self, query, candidates):
                    prior=self.by_query[query]
                    assert [(r['chunk_id'],r['content']) for r in candidates] == [(r['chunk_id'],r['content']) for r in prior['candidates']]
                    return tuple(prior['ordered_ids']),False
            arms=[(name, .5 if name.endswith('50') else .25,FrozenOrder(name)) for name in dict.fromkeys(r['arm'] for r in saved)]
        metadata={'replay_from':str(args.replay_from) if args.replay_from else None,'scope':__doc__,'dataset':ds.manifest,'query_sha256':hashlib.sha256(raw).hexdigest(),
            'query_provenance':'previously exposed assistant-authored development diagnostic; not blind gold',
            'generation':asdict(store.active_generation()),'local_reranker':local.identity,
            'budget':{'per_route':20,'candidate':20,'max_chunks':5,'context_tokens':2600},
            'api_limit':args.api_limit,'not_run':['conversation planning','generation','heldout']}
        (args.output/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,default=str)+'\n')
        rows=[]
        async def run_arm(name,weight,reranker):
            capture=CaptureReranker(reranker)
            api._knowledge_retriever=KnowledgeRetriever(candidate_source=source,transformer=NoRewrite(),reranker=capture,
                evidence_validator=PostgresKnowledgeEvidenceValidator(source))
            # Warm the local model without spending a measured query on loading.
            if name=='ce25' and not args.replay_from:
                _,failed=await local.rerank('model warmup',[{'chunk_id':'warmup','title':'warmup','content':'warmup'}])
                if failed:raise RuntimeError('local model warmup failed')
            for entry in queries:
                capture.rows=[];capture.ordered=();capture.fallback=None;handler_capture.clear();case=cases[entry['case_id']]
                query=entry['resolved_queries'][0];start=time.perf_counter()
                result=await tools.execute_for_agent('knowledge_search',{'query':query},agent_type='general',context={
                    'tenant_id':'rag-pair-dev','user_id':'eval-user','authorization_fingerprint':'isolated-eval',
                    'knowledge_as_of':'2026-09-07T00:00:00+00:00','cache_scope':'frozen-pair-v1',
                    'retrieval_policy':{'vector_weight':weight,'lexical_weight':1-weight,'query_expansion_count':0,'expansion_query_weight':0}})
                latency=(time.perf_counter()-start)*1000
                data=result.data if isinstance(result.data,dict) else {}
                handler_data=handler_capture.get('data') or {}
                pack=handler_data.get('evidence_pack') or {};items=pack.get('items',[])
                visible=json.loads(result.output_for_model) if result.success else {}
                hits={r['chunk_id']:{'document_id':r['source_id'],'source_start_char':r['source_start_char'],'source_end_char':r['source_end_char']} for r in capture.rows}
                metrics=evaluate_ranked_hits(case,capture.ordered,hits,top_k=5)
                row={'case_id':case.case_id,'arm':name,'query':query,'status':data.get('status',result.status),
                    'candidate_complete':covered(case,[(r['source_id'],r['source_start_char'],r['source_end_char']) for r in capture.rows]),
                    'top5_complete':covered(case,[(hits[c]['document_id'],hits[c]['source_start_char'],hits[c]['source_end_char']) for c in capture.ordered[:5]]),
                    'packed_complete':covered(case,[(r['source_ref']['source_id'],r['source_ref']['start_char'],r['source_ref']['end_char']) for r in items]),
                    'visible_complete':covered(case,[(r['source']['source_id'],r['source']['start_char'],r['source']['end_char']) for r in visible.get('evidence',[])]),
                    'metrics5':metrics,'fallback':capture.fallback,
                    'handler_result':handler_data,
                    'latency_ms':latency,'candidates':capture.rows,'ordered_ids':capture.ordered,'tool_result':data,'tool_message':result.output_for_model}
                rows.append(row)
                with (args.output/'cases.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
            group=[r for r in rows if r['arm']==name]
            print('ARM',name,{k:sum(bool(r[k]) for r in group) for k in ['candidate_complete','top5_complete','packed_complete','visible_complete']},flush=True)
        for arm in arms:await run_arm(*arm)
        if args.api_limit:
            from scripts.run_rag_tool_calibration import CaptureClient, AsyncAnthropic
            from core.model_policy import ModelPolicy,ModelRole
            from mcp.result_reranker import ResultReranker
            from scripts.run_rag_selected_composition_pair import clean
            values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
            policy=ModelPolicy.from_env(values)
            assert policy.profile(ModelRole.RERANK).model=='deepseek-v4-flash' and policy.profile(ModelRole.RERANK).provider=='deepseek'
            kwargs={'api_key':values['ANTHROPIC_API_KEY'],'max_retries':0,'timeout':60}
            if policy.base_url:kwargs['base_url']=policy.base_url
            client=CaptureClient(AsyncAnthropic(**kwargs),limit=args.api_limit)
            rr=ToolManagerRerankerAdapter(SimpleNamespace(_result_reranker=ResultReranker(client,policy.profile(ModelRole.RERANK))))
            metadata['listwise_version']=rr.version;metadata['listwise_profile']=policy.profile(ModelRole.RERANK).to_dict()
            await run_arm('listwise25',.25,rr)
            (args.output/'api-calls.json.gz').write_bytes(gzip.compress(json.dumps(clean(client.calls),ensure_ascii=False,default=str).encode(),mtime=0))
        summary={'api_calls':len(client.calls) if client else 0,'arms':{}}
        for name in dict.fromkeys(row['arm'] for row in rows):
            group=[r for r in rows if r['arm']==name];times=sorted(r['latency_ms'] for r in group)
            summary['arms'][name]={'cases':len(group),**{k:sum(bool(r[k]) for r in group) for k in ['candidate_complete','top5_complete','packed_complete','visible_complete','fallback']},
                'mrr5':sum(r['metrics5']['mrr'] for r in group)/len(group),'ndcg5_project':sum(r['metrics5']['ndcg'] for r in group)/len(group),'p50_ms':times[len(times)//2],'p95_ms':times[min(len(times)-1,int(len(times)*.95))]}
        summary['paired']={}
        by={(r['arm'],r['case_id']):r for r in rows}
        for left,right in [('fusion25','fusion50'),('fusion50','ce50'),('ce25','ce50'),('listwise25','ce50')]:
            if left not in summary['arms']:continue
            summary['paired'][left+'->'+right]={k:{'rescues':sum(not by[left,q['case_id']][k] and by[right,q['case_id']][k] for q in queries),'harms':sum(by[left,q['case_id']][k] and not by[right,q['case_id']][k] for q in queries)} for k in ['candidate_complete','top5_complete','packed_complete','visible_complete']}
        (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        (args.output/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,default=str)+'\n')
        (args.output/'cases.jsonl.gz').write_bytes(gzip.compress((args.output/'cases.jsonl').read_bytes(),mtime=0))
        print(json.dumps(summary),flush=True)
    finally:
        await tools._client.close()
        if client:await client.transport.close()
        retrieval.close();pool.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--queries',type=Path,required=True)
    p.add_argument('--embedding',type=Path,required=True);p.add_argument('--reranker',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--api-limit',type=int,default=0);p.add_argument('--replay-from',type=Path);args=p.parse_args()
    if args.replay_from and args.api_limit:raise ValueError('frozen ranking replay requires zero API calls')
    if not 0<=args.api_limit<=24:raise ValueError('bounded optional Flash limit 0..24')
    args.output.mkdir(parents=True,exist_ok=False)
    base=os.environ['TEST_DATABASE_URL'];parsed=urlsplit(base);name='dialogpilot_rag_pair_'+uuid.uuid4().hex[:12]
    with psycopg.connect(base,autocommit=True) as conn:conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url=urlunsplit((parsed.scheme,parsed.netloc,'/'+name,parsed.query,parsed.fragment))
    try:asyncio.run(evaluate(args,url))
    finally:
        with psycopg.connect(base,autocommit=True) as conn:
            conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(name,))
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))

if __name__=='__main__':main()
