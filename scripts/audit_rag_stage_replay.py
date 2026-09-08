"""Reconstruct recorded stages without APIs; missing route pools stay missing."""
import gzip,json,hashlib
from pathlib import Path
ROOTS=['rag-current-entry2-2026-09-08','rag-entry-multicondition4-2026-09-08','rag-manual-entry1-2026-09-08']

def strings(v):
    if isinstance(v,str):
        yield v
        if v[:1] in ('{','['):
            try: yield from strings(json.loads(v))
            except ValueError: pass
    elif isinstance(v,dict):
        for x in v.values():yield from strings(x)
    elif isinstance(v,list):
        for x in v:yield from strings(x)

def main():
    rows=[]
    for name in ROOTS:
        root=Path('artifacts/eval')/name/'run'
        for line in gzip.decompress((root/'full-cases.jsonl.gz').read_bytes()).splitlines():
            case=json.loads(line)
            reranks=[c for c in case['api_calls'] if any(t.get('name')=='submit_rerank' for t in c['request'].get('tools',[]))]
            searches=[t for t in case['tools'] if t['name']=='knowledge_search']
            row={'run':name,'case_id':case['case_id'],'outcome':case['outcome_type']}
            if not searches:
                rows.append({**row,'classification':'did_not_reach_retrieval'});continue
            # This witness corpus has one search and one uncached rerank per
            # completed execution. Do not guess alignment for general traces.
            assert len(searches)==len(reranks)==1
            data=searches[0]['result']['data'];call=reranks[0]
            prompt=next(s for s in strings(call['request']['messages']) if '\n候选：' in s)
            candidates=json.loads(prompt.split('\n候选：',1)[1])
            ids=[x[0] for x in data['trace']['source_ranks']]
            assert len(ids)==len(candidates) and len(set(ids))==len(ids)
            aliases={c['id']:i for c,i in zip(candidates,ids,strict=True)}
            blocks=call['response']['content']
            order=next(b['input']['ordered_ids'] for b in blocks if b.get('name')=='submit_rerank')
            assert len(order)==len(ids) and set(order)==set(aliases)
            texts={aliases[c['id']]:c['text'] for c in candidates}
            packed=data['evidence_pack']['items']
            assert all(texts[i['chunk_id']]==i['text'] for i in packed)
            # Exclude rerank and verifier: require evidence in the synthesis
            # request itself, rather than counting a verifier's copy as visibility.
            synth=[c for c in case['api_calls'] if not c['request'].get('tools')]
            visible=list(strings([c['request']['messages'] for c in synth]))
            rows.append({**row,'classification':'stages_reconstructed','query':data['evidence_pack']['query'],
                'candidate_count':len(ids),'candidate_ids':ids,'rerank_ids':[aliases[a] for a in order],
                'packed_ids':[i['chunk_id'] for i in packed],
                'packed_verbatim_in_synthesis':sum(any(i['text'] in s for s in visible) for i in packed),
                'full_pre_fusion_route_pools_saved':False,
                'candidates':[{'chunk_id':aliases[c['id']],'text_sha256':hashlib.sha256(c['text'].encode()).hexdigest()} for c in candidates]})
    out=Path('artifacts/eval/rag-stage-replay-audit-2026-09-08');out.mkdir(exist_ok=True)
    report={'api_calls':0,'scope':__doc__,'executions':len(rows),'reconstructed':sum(r['classification']=='stages_reconstructed' for r in rows),'rows':rows,
        'missing':['Full Dense/BM25 lists before fusion for actual Agent queries','Full provenance for candidates discarded before packing','Independent qrels for the synthetic full-chain cases'],
        'not_claimed':['Benchmark Recall or nDCG','Before-after answer improvement']}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False))

if __name__=='__main__':main()
