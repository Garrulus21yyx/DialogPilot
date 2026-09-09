"""Isolated SQL-only benchmark; synthetic corpus, production SQL, no models/API."""
import argparse,hashlib,json,os,statistics,subprocess,time,types,uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote,urlsplit,urlunsplit
import psycopg
from psycopg import sql
from tests.test_hybrid_retrieval_backends import _knowledge_request,_generation
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend

class Capture:
    def execute(self,q,p,**options): self.query,self.params,self.options=q,p,options;return self
    def fetchall(self):return []

def run(out,baseline):
    out.mkdir(parents=True,exist_ok=False)
    old_code=subprocess.check_output(['git','show',baseline+':infrastructure/hybrid_retrieval_backend.py'],text=True)
    old=types.ModuleType('baseline_bm25');exec(compile(old_code,'baseline_bm25','exec'),old.__dict__)
    url=os.environ.get('TEST_DATABASE_URL')
    if not url:
        config=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
        env=dict(v.split('=',1) for v in config['Config']['Env'] if '=' in v)
        url='postgresql://'+quote(env['POSTGRES_USER'],safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres'
    parts=urlsplit(url);name='dialogpilot_bm25_bench_'+uuid.uuid4().hex[:10]
    with psycopg.connect(url,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            with psycopg.connect(urlunsplit((parts.scheme,parts.netloc,'/'+name,'','')),autocommit=True) as c:
                c.execute('CREATE SCHEMA retrieval')
                c.execute('''CREATE TABLE retrieval.knowledge_chunk_search (
                    candidate_id text PRIMARY KEY, tenant_id text, generation_id text,
                    scope text, locale text, product text, source_type text, region text,
                    source_id text, source_revision text, provenance_sha256 text,
                    projected_at timestamptz DEFAULT now(), lexical_document text,
                    lexical_terms text[] GENERATED ALWAYS AS (string_to_array(lexical_document,' ')) STORED)''')
                c.execute('CREATE INDEX terms_gin ON retrieval.knowledge_chunk_search USING gin(lexical_terms)')
                c.execute('CREATE INDEX scoped_idx ON retrieval.knowledge_chunk_search(tenant_id,generation_id)')
                # 12k documents, 300 common tokens each; rare target in 1%,
                # English/Chinese terms, other-tenant rows and empty docs included.
                c.execute('''INSERT INTO retrieval.knowledge_chunk_search
                    (candidate_id,tenant_id,generation_id,scope,locale,product,source_type,region,
                     source_id,source_revision,provenance_sha256,lexical_document)
                    SELECT 'c-'||lpad(i::text,5,'0'), CASE WHEN i%10=0 THEN 'tenant-b' ELSE 'tenant-a' END,
                    'backend-generation-bench','public','zh-CN','payments','text','global',
                    'source-'||i,'r1',repeat('c',64),
                    CASE WHEN i%113=0 THEN '' ELSE repeat('common background ',150) ||
                    CASE WHEN i%101=0 THEN ' rare 退款 退 款 rare' ELSE ' normal' END END
                    FROM generate_series(1,12000) i''')
                c.execute('VACUUM ANALYZE retrieval.knowledge_chunk_search')
                results=[]
                for query in ['rare','退款','absent','common']:
                    request=replace(_knowledge_request(_generation('bench')),query_text=query,query_embedding=None,dense_limit=0,lexical_limit=20)
                    captured={}
                    for label,cls in [('before',old.PostgresHybridBackend),('after',PostgresHybridBackend)]:
                        cap=Capture();cls(None)._bm25(cap,request);captured[label]=cap
                    timings={'before':[],'after':[]};data={};plans={}
                    # Alternate order; include both planner-selected index and scan cases.
                    for i in range(6):
                        for label in (['before','after'] if i%2==0 else ['after','before']):
                            cap=captured[label];start=time.perf_counter()
                            data[label]=c.execute(cap.query,cap.params).fetchall()
                            timings[label].append((time.perf_counter()-start)*1000)
                    assert data['before']==data['after'],'IDs/scores/order changed'
                    for label,cap in captured.items():
                        plans[label]=c.execute(sql.SQL('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ')+cap.query,cap.params).fetchone()[0]
                        (out/(query+'-'+label+'-plan.json')).write_text(json.dumps(plans[label],ensure_ascii=False,indent=2)+'\n')
                    results.append({'query':query,'same_rows_scores_order':True,'rows':len(data['after']),
                        'times_ms':timings,'median_ms':{k:statistics.median(v) for k,v in timings.items()}})
                (out/'report.json').write_text(json.dumps({'baseline_ref':baseline,'documents':12000,
                    'scope_documents':10800,'repeat_runs':6,'corpus':'synthetic SQL workload, not ecommerce accuracy evaluation',
                    'settings':'planner defaults; no forced index, no production timeout changed',
                    'api_calls':0,'results':results},ensure_ascii=False,indent=2)+'\n')
                print(json.dumps(results,ensure_ascii=False,indent=2))
        finally:admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--baseline',default='f1ae53b');a=p.parse_args();run(a.output,a.baseline)
