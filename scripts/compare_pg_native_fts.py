"""Read-only lexical scorer comparison; no model or production config changes."""
import argparse, gzip, hashlib, json, math, statistics, subprocess, time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote
import psycopg
from psycopg import sql
from application.hybrid_retrieval import HybridRetrievalRequest, KnowledgeSearchScope, RetrievalCorpus
from application.chinese_lexical import postgres_lexical_document, postgres_websearch_or_query
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend, _scope_sql
from scripts.benchmark_bm25_index_access import Capture
from mcp.rank_fusion import fuse_rankings


def metrics(ranking, gold, sources):
    # Article labels: collapse repeated chunks only for article ranking metrics.
    articles=list(dict.fromkeys(sources[x] for x in ranking))
    hit=set(articles)&set(gold)
    dcg=sum(1/math.log2(i+2) for i,x in enumerate(articles[:5]) if x in gold)
    ideal=sum(1/math.log2(i+2) for i in range(min(5,len(gold))))
    return {'article_recall_at20_chunks':len(hit)/len(gold),
            'all_articles_at20_chunks':int(set(gold)<=set(articles)),
            'article_mrr':next((1/(i+1) for i,x in enumerate(articles) if x in gold),0),
            'article_ndcg_at5':dcg/ideal}


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(exist_ok=False)
    path=Path('artifacts/eval/wixqa-pg-compact-dev20-2026-09-08/cases.jsonl.gz')
    cases=[json.loads(l) for l in gzip.open(path,'rt')]
    cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
    env=dict(v.split('=',1) for v in cfg['Config']['Env'] if '=' in v)
    db='dialogpilot_wixqa_eval_20260908'
    url='postgresql://'+quote(env['POSTGRES_USER'],safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'+db
    backend=PostgresHybridBackend(None);results=[]
    with psycopg.connect(url,autocommit=True,prepare_threshold=None) as c:
        c.execute('SET default_transaction_read_only=on');c.execute("SET statement_timeout='30s'")
        g=json.loads(Path('artifacts/eval/wixqa-postgres-import-2026-09-08/report.json').read_text())['generation_id']
        # Stable provenance keys match the recorded vector ranking; no new embedding.
        rows=c.execute('select candidate_id,source_id,source_span from retrieval.knowledge_chunk_search where generation_id=%s',(g,)).fetchall()
        keys={};sources={}
        for cid,sid,prov in rows:
            keys[f"{sid}:{prov['start_char']}:{prov['end_char']}"]=cid;sources[cid]=sid
        manifest={'baseline_commit':'0c2e40e','database':db,'generation':g,'chunks':len(rows),'cases_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'backend_sha256':hashlib.sha256(Path('infrastructure/hybrid_retrieval_backend.py').read_bytes()).hexdigest(),'api_calls':0,'new_embeddings':0,'new_reranker_calls':0,'weights':[.5,.5],'rrf_k':10,'route_k':20,'fused_k':20,'repeats':3,'split':'previously consumed Wix dev20','normalization':0}
        (a.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        for ix,row in enumerate(cases):
            case=row['case'];q=case['query'];gold=case['article_ids']
            dense=[keys[x] for x in row['pg_routes']['raw:vector']];assert len(dense)==20
            req=HybridRetrievalRequest(tenant_id='wixqa-eval',corpus=RetrievalCorpus.KNOWLEDGE,backend_fingerprint='diagnostic',generation_id=g,policy_fingerprint='diagnostic',query_text=q,query_embedding=None,scope=KnowledgeSearchScope('public','en',as_of=datetime(2026,9,8,tzinfo=timezone.utc)),dense_limit=0,lexical_limit=20)
            _,_,_,_,filters,params=_scope_sql(req)
            # Audit the full matching universe, not just the Top-K intersection.
            audit=sql.SQL('SELECT count(*) FILTER (WHERE a<>b),count(*) FILTER(WHERE a),count(*) FILTER(WHERE b) FROM (SELECT lexical_terms && %s::text[] AS a, search_tsv @@ websearch_to_tsquery(\'simple\',%s) AS b FROM retrieval.knowledge_chunk_search WHERE tenant_id=%s AND generation_id=%s AND {}) x').format(filters)
            mismatch,bm_count,ft_count=c.execute(audit,(list(dict.fromkeys(postgres_lexical_document(q).split())),postgres_websearch_or_query(q),'wixqa-eval',g,*params)).fetchone()
            caps={}
            for arm,ranker in [('bm25','PG_BM25_ZH_V1'),('native','PG_FTS_ZH_V1')]:
                cap=Capture();backend._lexical(cap,req,ranker);caps[arm]=cap
            times={x:[] for x in caps};ranks={}
            for repeat in range(3):
                for arm in (['bm25','native'] if repeat%2==0 else ['native','bm25']):
                    cap=caps[arm];start=time.perf_counter();rs=c.execute(cap.query,cap.params,**cap.options).fetchall();times[arm].append(1000*(time.perf_counter()-start));ids=[r[0] for r in rs]
                    if arm in ranks:assert ranks[arm]==ids
                    ranks[arm]=ids
            budgets={}
            for arm,cap in caps.items():
                c.execute("SET statement_timeout='750ms'")
                try:c.execute(cap.query,cap.params,**cap.options).fetchall();budgets[arm]=True
                except psycopg.errors.QueryCanceled:budgets[arm]=False
                finally:c.execute("SET statement_timeout='30s'")
            scores={};fused={}
            for arm in caps:
                fused[arm]=fuse_rankings({'dense':dense,'lexical':ranks[arm]},weights={'dense':.5,'lexical':.5},rrf_k=10,top_k=20)
                scores[arm]={'lexical':metrics(ranks[arm],gold,sources),'fused':metrics(fused[arm],gold,sources)}
            result={'case':case,'matching_mismatch':mismatch,'match_counts':[bm_count,ft_count],'ranks':ranks,'dense':dense,'fused':fused,'scores':scores,'times_ms':times,'passed_750ms':budgets}
            results.append(result)
            (a.output/'cases.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
            print(ix+1,mismatch,{arm:round(statistics.median(t),1) for arm,t in times.items()},scores['bm25']['fused']['article_recall_at20_chunks'],scores['native']['fused']['article_recall_at20_chunks'],flush=True)
            if ix in (0,19):
                for arm,cap in caps.items():
                    plan=c.execute(sql.SQL('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ')+cap.query,cap.params,**cap.options).fetchone()[0]
                    (a.output/f'plan-{ix}-{arm}.json').write_text(json.dumps(plan,indent=2)+'\n')
    report={'case_count':len(results),'matching_mismatch_cases':sum(r['matching_mismatch']>0 for r in results),'arms':{}}
    for arm in caps:
        ts=[statistics.median(r['times_ms'][arm]) for r in results]
        report['arms'][arm]={'median_query_ms':statistics.median(ts),'empirical_p95_query_ms':sorted(ts)[math.ceil(.95*len(ts))-1],'timeouts_750ms':sum(not r['passed_750ms'][arm] for r in results),**{stage:{k:statistics.mean(r['scores'][arm][stage][k] for r in results) for k in scores[arm][stage]} for stage in ('lexical','fused')}}
    report['rescued']=sum(r['scores']['native']['fused']['all_articles_at20_chunks']>r['scores']['bm25']['fused']['all_articles_at20_chunks'] for r in results)
    report['harmed']=sum(r['scores']['native']['fused']['all_articles_at20_chunks']<r['scores']['bm25']['fused']['all_articles_at20_chunks'] for r in results)
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)

if __name__=='__main__':main()
