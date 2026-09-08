"""Dev-only exact source URL diagnostic; no inferred parents or new retrieval."""
import gzip,json,zipfile
from pathlib import Path
from collections import Counter
from urllib.parse import urlsplit
from scripts.adapt_mtrag_retrieval_dataset import digest,DOMAINS

BASE=Path('artifacts/eval')
def read(path):
    with gzip.open(path,'rt') as f:return json.load(f)

def valid_source_url(value):
    if not isinstance(value,str):return None
    try:
        parsed=urlsplit(value)
        return value if parsed.scheme in ("http","https") and parsed.hostname and not any(c.isspace() for c in value) else None
    except ValueError:return None

def main():
    dp=BASE/'rag-g4-mtrag-dense-complete-2026-09-08/results.json.gz'
    bp=BASE/'rag-g4-mtrag-lexical32-2026-09-07/results.json.gz'
    ds=read(dp);bs={r['case_id']:r for r in read(bp) if r['mode']=='rewrite'}
    needed={p for r in ds for p in r['gold']}
    for r in ds:
        needed.update(x['id'] for x in r['ranking'][:20]);needed.update(x['id'] for x in bs[r['case_id']]['ranking'][:20])
    sources={};counts=Counter();hashes={};url_states=Counter()
    for domain in DOMAINS:
        path=Path('/tmp/dialogpilot-mtrag-corpora-20260907')/f'{domain}.jsonl.zip'
        hashes[domain]=digest(path)
        manifest=json.loads((BASE/'rag-g4-mtrag-adapter-2026-09-07/manifest.json').read_text())
        if hashes[domain]!=manifest['source']['archives'][domain]['sha256']:raise ValueError('locked corpus mismatch')
        with zipfile.ZipFile(path) as z,z.open(domain+'.jsonl') as f:
            for line in f:
                r=json.loads(line)
                if not r['text'].strip():continue
                raw_url=r.get('url');url=valid_source_url(raw_url)
                url_states[domain+('::valid' if url else '::invalid' if raw_url else '::missing')]+=1
                if url:counts[domain,url]+=1
                pid=f"mtrag:{domain}:{r['_id']}"
                if pid in needed:sources[pid]=url
    assert set(sources)==needed
    result=[]
    for d in ds:
        b=bs[d['case_id']]
        assert all(d[k]==b[k] for k in ('query','domain','group_id','gold'))
        routes={'dense':[x['id'] for x in d['ranking'][:20]],'bm25':[x['id'] for x in b['ranking'][:20]]}
        union=set(routes['dense'])|set(routes['bm25'])
        missing=[]
        for gold in sorted(set(d['gold'])-union):
            url=sources[gold];locations={}
            for name,ids in routes.items():
                parents=list(dict.fromkeys(sources[p] for p in ids if sources[p]))
                locations[name]={'first_child_rank':next((i+1 for i,p in enumerate(ids) if url and sources[p]==url),None),'distinct_parent_rank':parents.index(url)+1 if url in parents else None}
            missing.append({'gold':gold,'url':url,'siblings':counts[d['domain'],url] if url else None,'locations':locations,'parent_in_top3':any(x['distinct_parent_rank'] is not None and x['distinct_parent_rank']<=3 for x in locations.values()),'parent_in_any_top20':any(x['first_child_rank'] is not None for x in locations.values())})
        result.append({'case_id':d['case_id'],'query':d['query'],'domain':d['domain'],'gold_count':len(d['gold']),'both_routes_no_gold':not bool(set(d['gold'])&union),'missing':missing})
    misses=[m for r in result for m in r['missing']]
    summary={'cases':len(result),'cases_missing_gold':sum(bool(r['missing']) for r in result),'both_routes_no_gold':sum(r['both_routes_no_gold'] for r in result),'missed_gold':len(misses),'missed_gold_with_url':sum(bool(m['url']) for m in misses),'same_source_in_top20':sum(m['parent_in_any_top20'] for m in misses),'same_source_in_top3_distinct':sum(m['parent_in_top3'] for m in misses)}
    out=BASE/'rag-g4-parent-opportunity32-2026-09-08';out.mkdir(exist_ok=False)
    (out/'report.json').write_text(json.dumps({'scope':'Exact URL opportunity only, consumed dev32; not measured child rescue','api_calls':0,'summary':summary,'url_states':dict(url_states),'source_hashes':hashes,'input_hashes':{str(p):digest(p) for p in (dp,bp)},'cases':result},indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    for r in result:
        if r['both_routes_no_gold']:print(json.dumps(r))
if __name__=='__main__':main()
