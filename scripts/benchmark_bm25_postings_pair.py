"""Read-only before/after equivalence and latency on the complete existing corpus."""
import json,subprocess,time,types,statistics,hashlib
from pathlib import Path
from urllib.parse import quote
from datetime import datetime,timezone
import psycopg
from psycopg import sql
from application.hybrid_retrieval import HybridRetrievalRequest,KnowledgeSearchScope,RetrievalCorpus
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from scripts.benchmark_bm25_index_access import Capture
ROOT=Path('artifacts/eval/bm25-postings-pair-final-2026-09-09')
def main(queries=None):
 ROOT.mkdir(parents=True,exist_ok=False)
 old=types.ModuleType('old');exec(subprocess.check_output(['git','show','3eee99f:infrastructure/hybrid_retrieval_backend.py'],text=True),old.__dict__)
 cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(x.split('=',1) for x in cfg['Config']['Env'] if '=' in x)
 db='dialogpilot_wixqa_eval_20260908';url='postgresql://'+quote(e['POSTGRES_USER'],safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'+db
 cases=json.loads(Path('data/eval/ecommerce-complex-v2/dev.inputs.json').read_text())
 queries=queries or [('original',cases[0]['message']),('rare','zzzxqnonexistent'),('chargeback','chargeback'),('common','the'),('fresh_shipping','How can I update shipping rates for international orders and exclude unsupported destinations?'),('fresh_negation','My customer has not received a refund. Does approval mean the money has arrived?')]
 out=[]
 with psycopg.connect(url,autocommit=True,prepare_threshold=None) as c:
  c.execute('SET default_transaction_read_only=on');c.execute("SET statement_timeout='30s'")
  g,tenant,scope,locale,n=c.execute('SELECT generation_id,tenant_id,scope,locale,count(*) FROM retrieval.knowledge_chunk_search GROUP BY 1,2,3,4 ORDER BY count(*) DESC LIMIT 1').fetchone()
  (ROOT/'manifest.json').write_text(json.dumps({'baseline':'3eee99f','current_sql_sha256':hashlib.sha256(Path('infrastructure/hybrid_retrieval_backend.py').read_bytes()).hexdigest(),'database':db,'generation':g,'chunks':n,'physical_rows':c.execute('SELECT count(*) FROM retrieval.knowledge_chunk_search').fetchone()[0],'queries':queries,'api_calls':0,'scope':'SQL only; no models, no production timeout/config changes','repeats':3},indent=2)+'\n')
  for label,q in queries:
   req=HybridRetrievalRequest(tenant_id=tenant,corpus=RetrievalCorpus.KNOWLEDGE,backend_fingerprint='diagnostic',generation_id=g,policy_fingerprint='diagnostic',query_text=q,query_embedding=None,scope=KnowledgeSearchScope(scope,locale,as_of=datetime(2026,9,10,tzinfo=timezone.utc),applicable_region='CN',applicable_channel='web'),dense_limit=0,lexical_limit=20)
   caps={};timings={'before':[],'after':[]};reference=None
   for name,cls in [('before',old.PostgresHybridBackend),('after',PostgresHybridBackend)]:
    cap=Capture();cls(None)._bm25(cap,req);caps[name]=cap
   for i in range(3):
    for name in (['before','after'] if i%2==0 else ['after','before']):
     cap=caps[name];start=time.perf_counter();rows=c.execute(cap.query,cap.params).fetchall();timings[name].append((time.perf_counter()-start)*1000)
     if reference is None:reference=rows
     assert rows==reference,(label,name,'ID/score/order drift')
   cap=caps['after'];p=c.execute(sql.SQL('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ')+cap.query,cap.params).fetchone()[0];(ROOT/(label+'-plan.json')).write_text(json.dumps(p,indent=2)+'\n')
   c.execute("SET statement_timeout='750ms'")
   try:
    rows=c.execute(cap.query,cap.params).fetchall();assert rows==reference;within=True
   except psycopg.errors.QueryCanceled:within=False
   finally:c.execute("SET statement_timeout='30s'")
   r={'query':label,'times_ms':timings,'median_ms':{name:statistics.median(v) for name,v in timings.items()},'same_ids_scores_order':True,'rows':len(reference),'after_passed_750ms':within};out.append(r);(ROOT/'report.json').write_text(json.dumps(out,indent=2)+'\n');print(label,r['median_ms'],within,flush=True)
if __name__=='__main__':main()
