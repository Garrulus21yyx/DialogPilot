"""Production BM25 SQL plans and equivalent-result latency on isolated PostgreSQL."""
import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit,urlunsplit
import uuid

import psycopg
from psycopg import sql

from application.hybrid_retrieval import HybridRetrievalRequest,KnowledgeSearchScope,RetrievalCorpus
from infrastructure.postgres import PostgresPool,PostgresPoolConfig,PostgresMigrationRunner
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from mcp.source_document import SourceDocument


class CaptureSQL:
    def execute(self,query,params):
        self.query=query.as_string();self.params=params;return self
    def fetchall(self):return []


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--reference-sql',type=Path);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    snapshot=json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-en-heldout-fresh-local-v1']
    docs=[json.loads(x) for x in snapshot['corpus.jsonl'].splitlines()]
    queries=json.loads(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/frozen-queries.json').read_text())[:3]
    base=os.environ['TEST_DATABASE_URL'];parts=urlsplit(base);name='dialogpilot_bm25_bench_'+uuid.uuid4().hex[:10]
    with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    url=urlunsplit((parts.scheme,parts.netloc,'/'+name,parts.query,parts.fragment));pool=None
    try:
        PostgresMigrationRunner(url).upgrade();pool=PostgresPool(PostgresPoolConfig(url,min_size=1,max_size=2));pool.open()
        store=PostgresKnowledgeStore(pool,tenant_id='bm25-bench')
        for start in range(0,len(docs),256):
            store.import_documents([SourceDocument.create(source_id=d['id'],title=d['title'],content=d['content'],source_type='text',effective_from=datetime(2025,1,1,tzinfo=timezone.utc)) for d in docs[start:start+256]])
        generation=store.active_generation();results=[]
        for index,(case_id,mode,query) in enumerate(queries):
            request=HybridRetrievalRequest(tenant_id='bm25-bench',corpus=RetrievalCorpus.KNOWLEDGE,backend_fingerprint=generation.backend_fingerprint,generation_id=generation.generation_id,policy_fingerprint='fixed-bm25-benchmark',query_text=query,query_embedding=None,dense_limit=0,lexical_limit=20,scope=KnowledgeSearchScope(scope='public',locale='zh-CN',as_of=datetime(2026,9,7,tzinfo=timezone.utc)))
            capture=CaptureSQL();PostgresHybridBackend(None)._bm25(capture,request)
            (args.output/'current.sql').write_text(capture.query.rstrip()+'\n')
            statements={'current':capture.query}
            if args.reference_sql:
                reference=args.reference_sql.read_text()
                ordered=reference.replace(') AS score,', 'ORDER BY tf.token) AS score,')
                statements={'reference':reference,'reference_ordered':ordered,**statements}
            record=dict(case_id=case_id,mode=mode,query=query,arms={})
            for arm,statement in statements.items():
                with pool.transaction() as c:
                    c.execute('SET LOCAL statement_timeout=30000')
                    plan=c.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+statement,capture.params).fetchone()[0]
                    times=[];rows=[]
                    for _ in range(3):
                        start=time.perf_counter();rows=c.execute(statement,capture.params).fetchall();times.append((time.perf_counter()-start)*1000)
                record['arms'][arm]=dict(plan=plan,latency_ms=times,ranked_scores=[(r[0],r[4]) for r in rows])
            if 'reference' in record['arms']:
                a=record['arms']['reference']['ranked_scores'];b=record['arms']['current']['ranked_scores']
                record['comparison'] = dict(ordered_ids_equal=[r[0] for r in a]==[r[0] for r in b], candidate_sets_equal=set(dict(a))==set(dict(b)), max_common_score_error=max((abs(dict(a)[key]-dict(b)[key]) for key in dict(a).keys() & dict(b).keys()), default=0))
            with pool.transaction() as c:
                c.execute('SET LOCAL statement_timeout=750')
                start=time.perf_counter()
                visible=c.execute(capture.query,capture.params).fetchall()
                record['budget_750ms'] = dict(success=True,elapsed_ms=(time.perf_counter()-start)*1000, rows=len(visible))
            if 'reference_ordered' in record['arms']:
                a=record['arms']['reference_ordered']['ranked_scores']
                b=record['arms']['current']['ranked_scores']
                record['ordered_reference_equal'] = a == b
            results.append(record)
            (args.output/'results.json').write_text(json.dumps(dict(api_calls=0,documents=len(docs),scope='lexical SQL only; placeholder hash embeddings; fixed production filters; diagnostic plan timeout30s',results=results),indent=2,default=str))
            if 'reference_ordered' in record['arms']:
                assert record['ordered_reference_equal'], 'ordered reference differs; see saved results'
            print('BENCH',case_id,mode,{k:v['latency_ms'] for k,v in record['arms'].items()},flush=True)
    finally:
        if pool:pool.close()
        with psycopg.connect(base,autocommit=True) as c:
            c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(name,))
            c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


if __name__=='__main__':main()
