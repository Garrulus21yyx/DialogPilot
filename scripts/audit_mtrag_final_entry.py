"""Audit full-collection real-query retrieval and source visibility, no API."""
import gzip,json,hashlib,re,zipfile
from pathlib import Path
ROOT=Path('artifacts/eval/rag-final-mtrag2-v3-2026-09-08')
def leaves(v):
    if isinstance(v,dict):return sum((leaves(x) for x in v.values()),[])
    if isinstance(v,list):return sum((leaves(x) for x in v),[])
    if isinstance(v,str):
        try:return [v]+leaves(json.loads(v))
        except (ValueError,TypeError):return [v]
    return []
def main():
    archive=Path('/tmp/dialogpilot-mtrag-corpora-20260907/cloud.jsonl.zip')
    manifest=json.loads(Path('/tmp/dialogpilot-mtrag-adapted-v2-20260907/manifest.json').read_text())
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==manifest['source']['archives']['cloud']['sha256']
    with zipfile.ZipFile(archive) as z,z.open('cloud.jsonl') as f:docs={'mtrag:cloud:'+d['_id']:d['text'] for d in map(json.loads,f) if d['text'].strip()}
    assert len(docs)==72439
    cases=json.loads((ROOT/'selection.json').read_text())['cases']
    definitions=json.loads((ROOT/'full-manifest.json').read_text())['cases']
    rows=[json.loads(l) for l in gzip.decompress((ROOT/'full-cases.jsonl.gz').read_bytes()).decode().splitlines()]
    routes=json.loads(gzip.decompress((ROOT/'source-route-captures.json.gz').read_bytes()))
    results=[]
    for c,d,r,s in zip(cases,definitions,rows,routes,strict=True):
        strings=leaves(r['api_calls'][0]['request'])
        assert all(any(text in v for v in strings) for role,text in d['history'])
        assert len(r['tools'])==1 and s['query']==r['tools'][0]['params']['query']
        assert s['collection_size']==len(docs)
        for candidate in s['fused_candidates']:
            text=docs[candidate['source_id']];assert candidate['content']==text
            assert hashlib.sha256(text.encode()).hexdigest()==candidate['source_checksum']
        wire=json.loads(r['tools'][0]['result']['output_for_model'])
        for e in wire['evidence']:
            ref=e['source'];text=docs[ref['source_id']]
            assert e['text']==text[ref['start_char']:ref['end_char']]
            assert ref['checksum']==hashlib.sha256(text.encode()).hexdigest()
        answer=r['outcome']['response']['response'];refs=re.findall(r'\[(E[^\]]+)\]',answer)
        assert all(ref in {e['evidence_id'] for e in wire['evidence']} for ref in refs)
        gold={e['document_id'] for e in c['evidence']}
        results.append({'case_id':c['id'],'query':s['query'],'history_complete_in_planner':True,'candidate_recall':len(gold&{e['source_id'] for e in s['fused_candidates']})/len(gold),'visible_recall':len(gold&{e['source']['source_id'] for e in wire['evidence']})/len(gold),'answer':answer,'api_calls':len(r['api_calls']),'latency_ms':r['outcome']['response'].get('latency_ms')})
    (ROOT/'audit.json').write_text(json.dumps({'cases':results,'full_collection':len(docs),'source_checks':True,'api_calls':sum(r['api_calls'] for r in results),'backend':'local full-collection reference backend; not PostgreSQL retrieval'},ensure_ascii=False,indent=2)+'\n')
    print('2 actual queries / histories / complete collection / citations audited')
if __name__=='__main__':main()
