"""Fixed Cloud dev queries: full-document BM25 vs existing child-derived parent ranks."""
import gzip,json,time
from pathlib import Path
from scripts.run_mtrag_lexical_query_pair import stream_bm25,TOKENIZER_VERSION
from scripts.audit_mtrag_document_mapping import readzip
from scripts.adapt_mtrag_retrieval_dataset import digest
B=Path('artifacts/eval')
def read(p):
    with gzip.open(p,'rt') as f:return json.load(f)
def score(ids,gold):
    return {**{f'recall@{k}':len(set(ids[:k])&gold)/len(gold) for k in (3,20)},'mrr@20':next((1/(i+1) for i,p in enumerate(ids[:20]) if p in gold),0.)}
def main():
    mapping_path=B/'rag-g4-document-mapping-2026-09-08/mapping.json.gz';mp=read(mapping_path)
    mapping_report=B/'rag-g4-document-mapping-2026-09-08/report.json'
    report=json.loads(mapping_report.read_text())
    corpus=Path('/tmp/dialogpilot-mtrag-documents-20260908/cloud.jsonl.zip')
    assert digest(corpus)==report['hashes']['cloud']['documents']
    dense_path=B/'rag-g4-mtrag-dense-complete-2026-09-08/results.json.gz';lexical_path=B/'rag-g4-mtrag-lexical32-2026-09-07/results.json.gz'
    ds=[r for r in read(dense_path) if r['domain']=='cloud'];bs={r['case_id']:r for r in read(lexical_path) if r['domain']=='cloud' and r['mode']=='rewrite'}
    docs=[('mtrag:cloud:'+r['document_id'],r['text']) for r in readzip(corpus,'cloud')]
    start=time.monotonic();ids,scores=stream_bm25([r['query'] for r in ds],iter(docs));elapsed=time.monotonic()-start
    results=[]
    for i,d in enumerate(ds):
        b=bs[d['case_id']];assert all(d[k]==b[k] for k in ('query','group_id','gold'))
        gold={mp[g] for g in d['gold']}
        ranks={name:list(dict.fromkeys(mp[x['id']] for x in row['ranking'][:20])) for name,row in [('dense_child',d),('bm25_child',b)]}
        order=sorted((j for j in range(len(ids)) if scores[i,j]>0),key=lambda j:(-scores[i,j],ids[j]))
        ranks['bm25_parent']=[ids[j] for j in order[:20]]
        child_union={x['id'] for row in (d,b) for x in row['ranking'][:20]}
        missing=[g for g in d['gold'] if g not in child_union]
        results.append({'case_id':d['case_id'],'query':d['query'],'gold_parents':sorted(gold),'ranks':ranks,'metrics':{a:score(r,gold) for a,r in ranks.items()},'missing_passage_parent_ranks':{g:next((k+1 for k,j in enumerate(order) if ids[j]==mp[g]),None) for g in missing}})
    summary={a:{m:sum(r['metrics'][a][m] for r in results)/len(results) for m in ('recall@3','recall@20','mrr@20')} for a in ranks}
    paired={a:{'better':sum(r['metrics']['bm25_parent']['recall@3']>r['metrics'][a]['recall@3'] for r in results),'worse':sum(r['metrics']['bm25_parent']['recall@3']<r['metrics'][a]['recall@3'] for r in results)} for a in ('dense_child','bm25_child')}
    out=B/'rag-g4-cloud-parent8-2026-09-08';out.mkdir(exist_ok=False)
    value={'scope':'Consumed Cloud dev8; parent relevance derived from passage qrels, not child or answer recall','api_calls':0,'embedding_calls':0,'bm25':{'k1':1.2,'b':.75,'tokenizer':TOKENIZER_VERSION},'documents':len(docs),'statistics_and_scoring_seconds':elapsed,'summary':summary,'paired_parent_recall3':paired,'hashes':{str(p):digest(p) for p in (corpus,mapping_path,mapping_report,dense_path,lexical_path)},'cases':results}
    (out/'report.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps({'summary':summary,'paired':paired,'seconds':elapsed},indent=2))
if __name__=='__main__':main()
