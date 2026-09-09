"""Reuse paired BM25 results; vary native ranking while keeping its match universe fixed."""
import argparse,json,statistics,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
import psycopg
from application.hybrid_retrieval import HybridRetrievalRequest,KnowledgeSearchScope,RetrievalCorpus
from application.chinese_lexical import postgres_lexical_document
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from scripts.benchmark_bm25_index_access import Capture
from scripts.compare_pg_native_fts import metrics
from mcp.rank_fusion import fuse_rankings

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);a=p.parse_args()
 original=json.loads((a.input/'cases.json').read_text());m=json.loads((a.input/'manifest.json').read_text());g=m['generation']
 cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(v.split('=',1) for v in cfg['Config']['Env'] if '=' in v)
 url='postgresql://'+quote(e['POSTGRES_USER'],safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'+m['database'];out=[]
 with psycopg.connect(url,autocommit=True,prepare_threshold=None) as c:
  c.execute('SET default_transaction_read_only=on');c.execute("SET statement_timeout='30s'")
  sources=dict(c.execute('select candidate_id,source_id from retrieval.knowledge_chunk_search where generation_id=%s',(g,)).fetchall())
  for row in original:
   q=row['case']['query'];req=HybridRetrievalRequest(tenant_id='wixqa-eval',corpus=RetrievalCorpus.KNOWLEDGE,backend_fingerprint='diagnostic',generation_id=g,policy_fingerprint='diagnostic',query_text=q,query_embedding=None,scope=KnowledgeSearchScope('public','en',as_of=datetime(2026,9,8,tzinfo=timezone.utc)),dense_limit=0,lexical_limit=20)
   cap=Capture();PostgresHybridBackend(None)._lexical(cap,req,'PG_FTS_ZH_V1')
   text=cap.query.as_string();assert text.count('search_tsv @@ query.value')==1
   text=text.replace('search_tsv @@ query.value','lexical_terms && %s::text[]')
   params=(*cap.params[:-1],list(dict.fromkeys(postgres_lexical_document(q).split())),cap.params[-1])
   timings=[];prior=None
   for _ in range(3):
    t=time.perf_counter();rows=c.execute(text,params,prepare=False).fetchall();timings.append(1000*(time.perf_counter()-t));ids=[x[0] for x in rows]
    if prior is not None:assert prior==ids
    prior=ids
   fused=fuse_rankings({'dense':row['dense'],'lexical':ids},weights={'dense':.5,'lexical':.5},rrf_k=10,top_k=20)
   scores={'lexical':metrics(ids,row['case']['article_ids'],sources),'fused':metrics(fused,row['case']['article_ids'],sources)}
   out.append({'case_id':row['case']['group_id'],'ranks':ids,'fused':fused,'scores':scores,'times_ms':timings})
   (a.input/'same-match-cases.json').write_text(json.dumps(out,indent=2)+'\n');print(len(out),scores['fused'],flush=True)
 report={stage:{k:statistics.mean(r['scores'][stage][k] for r in out) for k in out[0]['scores'][stage]} for stage in ('lexical','fused')}
 report['median_query_ms']=statistics.median(statistics.median(r['times_ms']) for r in out)
 (a.input/'same-match-report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
if __name__=='__main__':main()
