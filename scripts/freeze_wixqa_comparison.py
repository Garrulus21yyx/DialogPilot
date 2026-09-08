"""Freeze article-connected WixQA groups; no global freshness attestation."""
import gzip,json,hashlib
from pathlib import Path


def components(article_sets):
    parent=list(range(len(article_sets)))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    owner={}
    for i,articles in enumerate(article_sets):
        for article in articles:
            if article in owner:parent[find(i)]=find(owner[article])
            else:owner[article]=i
    groups={}
    for i in range(len(parent)):groups.setdefault(find(i),[]).append(i)
    return list(groups.values())


def main():
    cache=Path('/tmp/dialogpilot-rag-external-lock-20260907')
    lock=json.loads(Path('artifacts/eval/rag-three-dataset-lock-v3-2026-09-07/wixqa-test-lock.json').read_text())
    for name,spec in lock['source_files'].items():
        assert hashlib.sha256((cache/name).read_bytes()).hexdigest()==spec['sha256']
    audit_path=Path('artifacts/eval/rag-exposure-refresh-2026-09-08/inventory.json.gz')
    audit=json.loads(gzip.decompress(audit_path.read_bytes()))
    assert not audit['parse_errors'] and all(c['matches'] for c in audit['source_checks'])
    corpus=[json.loads(x) for x in (cache/'wix-corpus.jsonl').read_text().splitlines()];known={str(r['id']) for r in corpus}
    rows=[]
    for name in ('wix-expertwritten','wix-simulated'):
        data=[json.loads(x) for x in (cache/(name+'.jsonl')).read_text().splitlines()]
        assert len(data)==lock['configs'][name]['rows']
        flags={x['row_index']:x for x in audit['wixqa'][name]['matches']}
        for index,row in enumerate(data):
            articles=sorted(set(map(str,row['article_ids'])))
            assert articles and set(articles)<=known
            rows.append({'config':name,'row_index':index,'article_ids':articles,
                         'exposed':flags[index]['article_match'] or flags[index]['query_match']})
    groups=components([r['article_ids'] for r in rows]);selected={'dev':[],'heldout':[]};excluded=0;membership=[]
    for group in groups:
        articles=sorted({a for i in group for a in rows[i]['article_ids']})
        identity=hashlib.sha256(('wix-connected-v1\0'+'\0'.join(articles)).encode()).hexdigest()
        exposed=any(rows[i]['exposed'] for i in group)
        split='excluded' if exposed else 'heldout' if int(identity[:8],16)%5==0 else 'dev'
        excluded+=len(group) if exposed else 0
        membership.append({'group_id':identity,'split':split,'rows':[rows[i] for i in group]})
    # One query per component; deterministic config-balanced selection.
    for split in selected:
        counts={name:0 for name in ('wix-expertwritten','wix-simulated')}
        for group in sorted(membership,key=lambda g:g['group_id']):
            if group['split']!=split:continue
            options=sorted(group['rows'],key=lambda r:(counts[r['config']],r['config'],r['row_index']))
            chosen=next((r for r in options if counts[r['config']]<10),None)
            if chosen:
                selected[split].append({'group_id':group['group_id'],**chosen});counts[chosen['config']]+=1
        assert all(v==10 for v in counts.values()),counts
    dev={a for r in selected['dev'] for a in r['article_ids']};held={a for r in selected['heldout'] for a in r['article_ids']}
    assert not dev&held
    report={'dataset_revision':lock['revision'],'api_calls':0,'freshness_attested':False,'corpus_articles':len(corpus),'qa_rows':len(rows),'components':len(groups),'excluded_rows_by_connected_exposure':excluded,'selected':selected,'groups':membership,'audit_sha256':hashlib.sha256(audit_path.read_bytes()).hexdigest(),'plan':{'weights':[.25,.5],'rrf_k':10,'route_depth':20,'candidate_k':20,'final_k':5,'pack_tokens':2600,'reranker':'local BGE title+content; no truncation','metrics':'article qrels only; not evidence-span recall','heldout_usage':'reserved; no scores computed'}}
    out=Path('artifacts/eval/wixqa-connected-comparison-2026-09-08');out.mkdir(exist_ok=False)
    (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('groups','selected')},indent=2))

if __name__=='__main__':main()
