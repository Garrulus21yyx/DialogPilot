"""Read-only production SQL diagnosis against an existing complete evaluation DB."""
import json,subprocess,hashlib,time
from pathlib import Path
from urllib.parse import quote
from datetime import datetime,timezone
import psycopg
from psycopg import sql
from application.hybrid_retrieval import HybridRetrievalRequest,KnowledgeSearchScope,RetrievalCorpus
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from scripts.benchmark_bm25_index_access import Capture

ROOT=Path('artifacts/eval/bm25-planner-diagnosis-2026-09-09')
DB='dialogpilot_wixqa_eval_20260908'
def main():
 ROOT.mkdir(parents=True,exist_ok=True)
 cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];env=dict(v.split('=',1) for v in cfg['Config']['Env'] if '=' in v)
 url='postgresql://'+quote(env['POSTGRES_USER'],safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'+DB
 with psycopg.connect(url,autocommit=True,prepare_threshold=None) as c:
  c.execute('SET default_transaction_read_only=on');c.execute("SET statement_timeout='30s'")
  g,s,l,n=c.execute('SELECT generation_id,scope,locale,count(*) FROM retrieval.knowledge_chunk_search GROUP BY 1,2,3 ORDER BY count(*) DESC LIMIT 1').fetchone()
  tenant=c.execute('SELECT tenant_id FROM retrieval.knowledge_chunk_search WHERE generation_id=%s LIMIT 1',(g,)).fetchone()[0]
  meta={'database':DB,'generation_id':g,'chunks':n,'physical_rows':c.execute('SELECT count(*) FROM retrieval.knowledge_chunk_search').fetchone()[0],
   'server':c.execute('SELECT version()').fetchone()[0],'indexes':c.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='retrieval' AND tablename='knowledge_chunk_search'").fetchall(),
   'table_statistics':c.execute("SELECT n_live_tup,last_analyze,last_autoanalyze FROM pg_stat_all_tables WHERE schemaname='retrieval' AND relname='knowledge_chunk_search'").fetchall(),
   'settings':c.execute("SELECT name,setting FROM pg_settings WHERE name IN ('random_page_cost','seq_page_cost','effective_cache_size','work_mem','plan_cache_mode')").fetchall(),
   'scope':'Existing Wix corpus, not deleted E12 ecommerce DB; SQL only, no API or models','head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
  (ROOT/'manifest.json').write_text(json.dumps(meta,indent=2,default=str)+'\n')
  original=json.loads(Path('data/eval/ecommerce-complex-v2/dev.inputs.json').read_text())[0]['message']
  results=[]
  for label,text in [('ecommerce_raw',original),('rare','zzzxqnonexistent'),('specific','chargeback')]:
   request=HybridRetrievalRequest(tenant_id=tenant,corpus=RetrievalCorpus.KNOWLEDGE,backend_fingerprint='diagnostic',generation_id=g,policy_fingerprint='diagnostic',query_text=text,query_embedding=None,scope=KnowledgeSearchScope(s,l,as_of=datetime(2026,9,10,tzinfo=timezone.utc),applicable_region='CN',applicable_channel='web'),dense_limit=0,lexical_limit=20)
   cap=Capture();PostgresHybridBackend(None)._bm25(cap,request)
   reference=None
   for mode in ['default','no_nestloop']:
    c.execute('SET enable_nestloop='+('on' if mode=='default' else 'off'))
    start=time.perf_counter()
    try:
     rows=c.execute(cap.query,cap.params).fetchall();elapsed=(time.perf_counter()-start)*1000
    except psycopg.errors.QueryCanceled:
     results.append({'query':label,'mode':mode,'error':'statement_timeout_30s'})
     (ROOT/'report.json').write_text(json.dumps(results,indent=2)+'\n');print(label,mode,'TIMEOUT',flush=True)
     p=c.execute(sql.SQL('EXPLAIN (SETTINGS, FORMAT JSON) ')+cap.query,cap.params).fetchone()[0]
     (ROOT/(label+'-'+mode+'-estimated-plan.json')).write_text(json.dumps(p,indent=2)+'\n')
     continue
    if reference is None:reference=rows
    assert rows==reference
    p=c.execute(sql.SQL('EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) ')+cap.query,cap.params).fetchone()[0]
    (ROOT/(label+'-'+mode+'-plan.json')).write_text(json.dumps(p,indent=2)+'\n')
    nodes=[]
    def walk(n):
     nodes.append({k:n[k] for k in ['Node Type','Subplan Name','Relation Name','Index Name','Plan Rows','Actual Rows','Actual Loops','Actual Total Time','Total Cost','Rows Removed by Filter','Shared Hit Blocks','Shared Read Blocks'] if k in n})
     for ch in n.get('Plans',[]):walk(ch)
    walk(p[0]['Plan'])
    r={'query':label,'mode':mode,'sql_ms':elapsed,'explain_ms':p[0]['Execution Time'],'same_rows_scores_order':True,'rows':len(rows),'nodes':nodes};results.append(r)
    (ROOT/'report.json').write_text(json.dumps(results,indent=2)+'\n');print(label,mode,round(elapsed,2),round(p[0]['Execution Time'],2),flush=True)
if __name__=='__main__':main()
