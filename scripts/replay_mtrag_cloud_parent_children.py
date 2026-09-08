"""Single preregistered bounded parent-local BM25 branch, dev-only candidate diagnostic."""
import gzip,json
from pathlib import Path
from collections import defaultdict
from scripts.audit_mtrag_document_mapping import readzip
from scripts.run_mtrag_lexical_query_pair import stream_bm25
from scripts.adapt_mtrag_retrieval_dataset import digest
B=Path('artifacts/eval')
def read(p):
    with gzip.open(p,'rt') as f:return json.load(f)
def main():
    parent_path=B/'rag-g4-cloud-parent8-2026-09-08/report.json';report=json.loads(parent_path.read_text())
    mp=read(B/'rag-g4-document-mapping-2026-09-08/mapping.json.gz')
    ds={r['case_id']:r for r in read(B/'rag-g4-mtrag-dense-complete-2026-09-08/results.json.gz')}
    pp=Path('/tmp/dialogpilot-mtrag-corpora-20260907/cloud.jsonl.zip')
    mr=json.loads((B/'rag-g4-document-mapping-2026-09-08/report.json').read_text());assert digest(pp)==mr['hashes']['cloud']['passages']
    wanted={p for r in report['cases'] for p in r['ranks']['bm25_parent'][:3]};children=defaultdict(list)
    for r in readzip(pp,'cloud'):
        pid='mtrag:cloud:'+r['_id']
        if mp.get(pid) in wanted:children[mp[pid]].append((pid,r['text']))
    rows=[]
    for r in report['cases']:
        d=ds[r['case_id']];assert d['query']==r['query'];local=[];trace=[]
        for parent in r['ranks']['bm25_parent'][:3]:
            ids,scores=stream_bm25([d['query']],iter(children[parent]))
            rank=sorted((i for i in range(len(ids)) if scores[0,i]>0),key=lambda i:(-scores[0,i],ids[i]))
            chosen=[ids[i] for i in rank[:2]];local+=chosen
            trace.append({'parent':parent,'pool_size':len(ids),'chosen':chosen,'gold_local_ranks':{g:next((k+1 for k,i in enumerate(rank) if ids[i]==g),None) for g in d['gold'] if mp[g]==parent}})
        baseline=[x['id'] for x in d['ranking'][:20]];selected=list(dict.fromkeys(local+baseline))[:20];gold=set(d['gold'])
        assert len(selected)<=20 and set(local)<=set(selected)
        rows.append({'case_id':d['case_id'],'query':d['query'],'gold':d['gold'],'baseline':baseline,'candidate':selected,'parent_trace':trace,'baseline_recall':len(gold&set(baseline))/len(gold),'candidate_recall':len(gold&set(selected))/len(gold),'rescued':sorted((gold&set(selected))-set(baseline)),'lost':sorted((gold&set(baseline))-set(selected))})
    summary={'baseline_recall20':sum(r['baseline_recall'] for r in rows)/len(rows),'candidate_recall20':sum(r['candidate_recall'] for r in rows)/len(rows),'better':sum(r['candidate_recall']>r['baseline_recall'] for r in rows),'worse':sum(r['candidate_recall']<r['baseline_recall'] for r in rows)}
    out=B/'rag-g4-cloud-parent-child8-2026-09-08';out.mkdir(exist_ok=False)
    (out/'report.json').write_text(json.dumps({'scope':'Dev8 candidate only; local branch reserves up to6 of20; no CE/pack/answer','api_calls':0,'summary':summary,'parent_report_sha256':digest(parent_path),'cases':rows},indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
