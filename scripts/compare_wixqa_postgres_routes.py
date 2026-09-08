"""Read-only PG vs offline frozen WixQA dev routes; cached query vectors."""
import argparse,asyncio,gzip,json,subprocess
from pathlib import Path
from urllib.parse import quote
import numpy as np
from application.knowledge_retriever import KnowledgeRetrievalRequest,KnowledgeRetrievalPolicy
from infrastructure.retrieval_postgres import RetrievalPostgresPool,RetrievalPoolConfig,PostgresRetrievalGenerationRegistry
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource

async def main():
 parser=argparse.ArgumentParser();parser.add_argument('--split',choices=('dev','heldout'),default='dev');parser.add_argument('--output',type=Path,default=Path('artifacts/eval/wixqa-pg-routes-dev20-2026-09-08'));args=parser.parse_args()
 root=args.output;root.mkdir(exist_ok=False)
 old=Path(f'artifacts/eval/wixqa-fixed-{args.split}20-2026-09-08')
 cases=[json.loads(l) for l in (old/'cases.jsonl').read_text().splitlines()]
 vectors=np.load(old/'query-vectors.npy',allow_pickle=False);assert vectors.shape==(20,1024)
 queries={r['case']['query']:tuple(map(float,v)) for r,v in zip(cases,vectors,strict=True)}
 c=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
 url='postgresql://'+quote(e.get('POSTGRES_USER','postgres'),safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/dialogpilot_wixqa_eval_20260908'
 pool=RetrievalPostgresPool(RetrievalPoolConfig(url,min_size=1,max_size=3));pool.open()
 try:
  registry=PostgresRetrievalGenerationRegistry(pool)
  info=json.loads(Path('artifacts/eval/wixqa-postgres-import-2026-09-08/report.json').read_text());g=registry.get(info['generation_id'])
  source=PostgresKnowledgeCandidateSource(backend=PostgresHybridBackend(pool),generations=registry,pool=pool,embed_query=lambda query,generation:queries[query])
  results=[]
  for r in cases:
   q=r['case']['query'];req=KnowledgeRetrievalRequest(tenant_id='wixqa-eval',user_scope='eval',authorization_fingerprint='isolated-public-eval',acl_policy_fingerprint='public',deletion_epoch=0,requirement_signature='knowledge.active_source',query=q,history=(),conversation_range_hash='standalone',locale='en',product=None,manifest_fingerprint=g.manifest_hash,generation_id=g.generation_id,policy=KnowledgeRetrievalPolicy(policy_version="eval-fixed",backend_fingerprint=g.backend_fingerprint,lexical_provider="PG_BM25_ZH_V1",transformer_version="resolved",embedding_version=g.embedding_profile.fingerprint,reranker_version="unused",packer_version="unused"),query_mode='RESOLVED')
   got=await source.capture_source_rankings_async(req,[('raw',q,1.0)],dense_k=20,lexical_k=20)
   if got.status.value!='OK':
    failure={'case':r['case'],'status':got.status.value,'detail_code':got.detail_code}
    with (root/'failures.jsonl').open('a') as f:f.write(json.dumps(failure)+'\n')
    print('failure',got.detail_code,flush=True)
    continue
   routes={}
   for route in ('raw:vector','raw:bm25'):
    ranked=sorted((x for x in got.candidates if route in x['ranks']),key=lambda x:x['ranks'][route])
    routes[route]=[f"{x['source_id']}:{x['source_start_char']}:{x['source_end_char']}" for x in ranked]
   comparison={route:{'same_order':routes[route]==r['routes'][offline],'overlap':len(set(routes[route])&set(r['routes'][offline]))} for route,offline in [('raw:vector','dense'),('raw:bm25','bm25')]}
   row={'case':r['case'],'pg_routes':routes,'comparison':comparison,'candidates':list(got.candidates)};results.append(row)
   with (root/'cases.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
   print(len(results),comparison,flush=True)
  report={'successful_cases':len(results),'requested_cases':len(cases),'api_calls':0,'new_embeddings':0,'routes':{route:{'identical_order':sum(r['comparison'][route]['same_order'] for r in results),'mean_overlap_at20':sum(r['comparison'][route]['overlap'] for r in results)/len(results)} for route in routes}}
  (root/'report.json').write_text(json.dumps(report,indent=2)+'\n');(root/'cases.jsonl.gz').write_bytes(gzip.compress((root/'cases.jsonl').read_bytes(),mtime=0));print(json.dumps(report))
 finally:pool.close()

if __name__=='__main__':asyncio.run(main())
