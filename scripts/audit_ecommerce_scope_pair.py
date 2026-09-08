"""Audit actual paired request/candidate invariants without opening relevance labels."""
import argparse,gzip,hashlib,json
from collections import Counter
from datetime import datetime
from pathlib import Path

def audit(root,corpus):
    docs={d['source_id']:d for d in json.loads(corpus.read_text())}
    rows=[json.loads(line) for line in gzip.decompress((root/'runtime/pure-cases.jsonl.gz').read_bytes()).splitlines()]
    groups={}
    for row in rows:groups.setdefault(row['id'],{})[row['arm']]=row
    assert len(rows)==2*len(groups)
    checked=0;universal=0;failures=[]
    for cid,pair in groups.items():
        a,b=(pair[k] for k in ('scope_unfiltered','scope_filtered'))
        assert a['query']==b['query'] and a['history']==b['history'] and a['original']==b['original']
        assert a['retrieval'] and b['retrieval']
        x,y=a['retrieval'][-1],b['retrieval'][-1]
        assert x['variants']==y['variants'] and x['top_k']==y['top_k']==20
        left,right=x['request_scope'],y['request_scope']
        for k in ('tenant_id','generation_id','manifest_fingerprint','as_of','as_of_end'):
            assert left[k]==right[k],(cid,k)
        assert left['applicable_region'] is None and left['applicable_channel'] is None
        assert right['applicable_region']=='CN' and right['applicable_channel']=='web'
        as_of=datetime.fromisoformat(right['as_of'])
        for c in y['fused_candidates']:
            d=docs[c['source_id']];m=d['metadata']
            assert m.get('region','global') in ('CN','global')
            assert m.get('channel','global') in ('web','global')
            assert not m.get('effective_from') or datetime.fromisoformat(m['effective_from'])<=as_of
            assert not m.get('effective_to') or as_of<datetime.fromisoformat(m['effective_to'])
            assert d['content'][c['source_start_char']:c['source_end_char']]==c['content']
            checked+=1;universal+=int(m.get('origin')=='WixQA')
        for row in (a,b):
            if row['result']['status']!='OK':failures.append({'id':cid,'arm':row['arm'],'status':row['result']['status'],'detail_code':row['result'].get('detail_code')})
    assert universal>0,'Universal background must remain eligible and reachable; this is not a gold-only corpus'
    result={'pairs':len(groups),'checked_filtered_candidates':checked,'universal_external_candidates':universal,'failures':failures,'status_counts':dict(Counter(r['result']['status'] for r in rows)),'reranker_fallbacks':sum(bool(c.get('fallback')) for r in rows for c in r['rerank']),'trace_sha256':hashlib.sha256((root/'runtime/pure-cases.jsonl.gz').read_bytes()).hexdigest(),'scope':'Actual request, shared query/snapshot, candidate applicability and provenance; no gold read'}
    (root/'scope-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--corpus',type=Path,required=True);a=p.parse_args();audit(a.root,a.corpus)
