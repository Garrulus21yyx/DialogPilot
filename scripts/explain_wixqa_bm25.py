"""Read-only EXPLAIN of the production BM25 statement on a fixed failed query."""
import argparse,json,subprocess
from pathlib import Path
from urllib.parse import quote
from datetime import datetime,timezone
import psycopg
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from application.hybrid_retrieval import HybridRetrievalRequest,KnowledgeSearchScope,RetrievalCorpus

class Capture:
 def execute(self,query,params):self.query,self.params=query,params;return self
 def fetchall(self):return []

def main():
 p=argparse.ArgumentParser();p.add_argument('--label',default='before');a=p.parse_args()
 root=Path('artifacts/eval/wixqa-bm25-plan-2026-09-08');root.mkdir(exist_ok=True)
 failed=json.loads(Path('artifacts/eval/wixqa-pg-routes-dev20-2026-09-08/failures.jsonl').read_text().splitlines()[0]);query=failed['case']['query']
 g=json.loads(Path('artifacts/eval/wixqa-postgres-import-2026-09-08/report.json').read_text())['generation_id']
 req=HybridRetrievalRequest(tenant_id='wixqa-eval',corpus=RetrievalCorpus.KNOWLEDGE,backend_fingerprint='POSTGRES_PGVECTOR_PG_BM25_ZH_V1',generation_id=g,policy_fingerprint='diagnostic',query_text=query,query_embedding=None,scope=KnowledgeSearchScope(scope='public',locale='en',product=None,as_of=datetime(2026,9,8,tzinfo=timezone.utc)),dense_limit=0,lexical_limit=20)
 capture=Capture();PostgresHybridBackend(None)._bm25(capture,req)
 c=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
 url='postgresql://'+quote(e.get('POSTGRES_USER','postgres'),safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/dialogpilot_wixqa_eval_20260908'
 with psycopg.connect(url) as conn:
  conn.execute("SET LOCAL statement_timeout='30s'")
  text=capture.query.as_string(conn)
  plan=conn.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+text,capture.params).fetchone()[0]
  rows=conn.execute(capture.query,capture.params).fetchall()
 result={'query':query,'sql':text,'params':capture.params,'plan':plan,'ranked_ids':[r[0] for r in rows],'scores':[r[4] for r in rows],'diagnostic_timeout_seconds':30,'production_timeout_changed':False}
 (root/f'{a.label}.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
 print('Execution ms',plan[0]['Execution Time'])
 def walk(n):
  if n.get('Actual Total Time',0)>50 or n.get('Actual Loops',0)>1000:print(n['Node Type'],n.get('Actual Total Time'),n.get('Actual Rows'),n.get('Actual Loops'),n.get('Temp Written Blocks'),n.get('Subplan Name',''))
  for child in n.get('Plans',[]):walk(child)
 walk(plan[0]['Plan'])
if __name__=='__main__':main()
