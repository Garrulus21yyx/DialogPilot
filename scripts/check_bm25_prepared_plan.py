"""Check generic prepared plans against custom plans with identical SQL results."""
import json,subprocess,time
from pathlib import Path
from urllib.parse import quote
from datetime import datetime,timezone
import psycopg
from application.hybrid_retrieval import HybridRetrievalRequest,KnowledgeSearchScope,RetrievalCorpus
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from scripts.benchmark_bm25_index_access import Capture
if __name__=='__main__':
 root=Path('artifacts/eval/bm25-postings-pair-final-2026-09-09');m=json.loads((root/'manifest.json').read_text());cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(v.split('=',1) for v in cfg['Config']['Env'] if '=' in v);u='postgresql://'+quote(e['POSTGRES_USER'],safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'+m['database'];out=[]
 with psycopg.connect(u,autocommit=True,prepare_threshold=0) as c:
  c.execute('SET default_transaction_read_only=on');c.execute("SET statement_timeout='30s'")
  tenant,scope,locale=c.execute('SELECT tenant_id,scope,locale FROM retrieval.knowledge_chunk_search WHERE generation_id=%s LIMIT 1',(m['generation'],)).fetchone()
  for label,q in [m['queries'][0],m['queries'][2],m['queries'][5]]:
   req=HybridRetrievalRequest(tenant_id=tenant,corpus=RetrievalCorpus.KNOWLEDGE,backend_fingerprint='diagnostic',generation_id=m['generation'],policy_fingerprint='diagnostic',query_text=q,query_embedding=None,scope=KnowledgeSearchScope(scope,locale,as_of=datetime(2026,9,10,tzinfo=timezone.utc),applicable_region='CN',applicable_channel='web'),dense_limit=0,lexical_limit=20)
   cap=Capture();PostgresHybridBackend(None)._bm25(cap,req);reference=None;r={'query':label}
   for mode in ['force_custom_plan','force_generic_plan','auto']:
    c.execute('SET plan_cache_mode='+mode);start=time.perf_counter();rows=PostgresHybridBackend(None)._bm25(c,req);r[mode]=(time.perf_counter()-start)*1000
    if reference is None:reference=rows
    assert rows==reference
   for _ in range(6):
    assert PostgresHybridBackend(None)._bm25(c,req)==reference
   r['auto_prepared_bm25_count']=c.execute("SELECT count(*) FROM pg_prepared_statements WHERE ltrim(statement) LIKE 'WITH query_terms AS%'").fetchone()[0]
   assert r['auto_prepared_bm25_count']==0
   r['same_results']=True;out.append(r);print(r,flush=True)
 (root/'prepared-plans.json').write_text(json.dumps(out,indent=2)+'\n')
