"""Evidence-unit coverage and binary relevant-chunk nDCG; dev split only."""
import argparse,gzip,json,math
from pathlib import Path
from mcp.document_chunker import DocumentChunker
ROOT=Path('data/eval/ecommerce-complex-v2')

def covers(c,e):
    return c['source_id']==e['source_id'] and c['source_start_char']<=e['start_char'] and c['source_end_char']>=e['end_char'] and e['quote'] in c['content']

def metrics(candidates,units,relevant_count):
    out={}
    for k in [5,20]:
        matched=[any(covers(c,e) for c in candidates[:k]) for e in units]
        out[f'complete_r{k}']=int(all(matched));out[f'unit_r{k}']=sum(matched)/len(units)
    relevant=[i for i,c in enumerate(candidates,1) if any(covers(c,e) for e in units)]
    out['mrr5']=1/min(relevant) if relevant and min(relevant)<=5 else 0
    ideal=sum(1/math.log2(i+1) for i in range(1,min(5,relevant_count)+1))
    out['ndcg5']=sum(1/math.log2(i+1) for i in relevant if i<=5)/ideal if ideal else 0
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--corpus',type=Path);a=p.parse_args()
    labels={r['id']:r for r in json.loads((ROOT/'dev.gold.json').read_text())}
    docs=json.loads((a.corpus or ROOT/'corpus.json').read_text());chunks=[]
    for d in docs:
        chunks += [{'source_id':d['source_id'],'source_start_char':c.start_char,'source_end_char':c.end_char,'content':c.content} for c in DocumentChunker().split(d['content'],max_tokens=512,overlap_tokens=64,source_type=d['metadata']['source_type'])]
    rows=[json.loads(l) for l in gzip.decompress((a.root/'runtime/pure-cases.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows)==80 and len({(r['id'],r['arm']) for r in rows})==80
    scored=[]
    for row in rows:
        units=labels[row['id']]['evidence_units'];gold_count=sum(any(covers(c,e) for e in units) for c in chunks)
        candidates=row['retrieval'][-1]['fused_candidates'] if row['retrieval'] else []
        mapping={c['chunk_id']:c for c in candidates};ordered=row['rerank'][-1]['ordered_ids'] if row['rerank'] else []
        assert not candidates or set(mapping)==set(ordered)
        wire=[{'source_id':e['source']['source_id'],'source_start_char':e['source']['start_char'],'source_end_char':e['source']['end_char'],'content':e['text']} for e in row['wire'].get('evidence',[])]
        for c in candidates+wire:
            body=next(d['content'] for d in docs if d['source_id']==c['source_id']);assert body[c['source_start_char']:c['source_end_char']]==c['content']
        scored.append({'id':row['id'],'arm':row['arm'],'query':row['query'],
            **{stage:metrics(cs,units,gold_count) for stage,cs in [('candidate',candidates),('rerank',[mapping[x] for x in ordered]),('wire',wire)]},
            'wrong_scope_top5':sum(c['source_id'] in labels[row['id']]['forbidden_sources'] or c['source_id'].startswith('complex:scope-negative:') for c in wire[:5]),
            'external_background_top5':sum(c['source_id'].startswith('wix:') for c in wire[:5]),
            'candidate_count':len(candidates),
            'status':row['result']['status']})
    summary={}
    for arm in ['history_concat','standalone_raw']:
        rr=[r for r in scored if r['arm']==arm]
        summary[arm]={stage:{k:sum(r[stage][k] for r in rr)/len(rr) for k in rr[0][stage]} for stage in ['candidate','rerank','wire']}
        summary[arm]['diagnostics']={'candidate_count_min':min(r['candidate_count'] for r in rr),'candidate_count_max':max(r['candidate_count'] for r in rr),'cases_with_wrong_scope_in_wire':sum(r['wrong_scope_top5']>0 for r in rr),'wrong_scope_wire_chunks':sum(r['wrong_scope_top5'] for r in rr),'external_background_wire_chunks':sum(r['external_background_top5'] for r in rr)}
    changes=[]
    for cid in labels:
        b,c=([r for r in scored if r['id']==cid and r['arm']==arm][0] for arm in ['history_concat','standalone_raw'])
        changes.append({'id':cid,'wire_delta':c['wire']['complete_r5']-b['wire']['complete_r5'],'both_available':b['status']=='OK' and c['status']=='OK','baseline_complete':b['wire']['complete_r5'],'candidate_complete':c['wire']['complete_r5']})
    unique={e['unit_id']:e for row in labels.values() for e in row['evidence_units']}
    result={'n':40,'groups':10,'documents':len(docs),'chunks':len(chunks),'gold_units':len(unique),'chunk_containment':sum(any(covers(c,e) for c in chunks) for e in unique.values())/len(unique),'summary':summary,'rescue':sum(x['wire_delta']>0 for x in changes),'hurt':sum(x['wire_delta']<0 for x in changes),'definition':'Complete = all 3 annotated source clauses covered; unit recall averages 3 binary coverage labels. nDCG uses binary relevance of whole chunks containing any gold unit, ideal from complete corpus chunks. Not answer correctness. Only dev opened for scoring.'}
    available=[x for x in changes if x['both_available']]
    result['availability_failures']=[{'id':r['id'],'arm':r['arm'],'status':r['status']} for r in scored if r['status']!='OK']
    result['both_available_sensitivity']={'n':len(available),'baseline_complete':sum(x['baseline_complete'] for x in available),'candidate_complete':sum(x['candidate_complete'] for x in available),'rescue':sum(x['wire_delta']>0 for x in available),'hurt':sum(x['wire_delta']<0 for x in available),'note':'Diagnostic only; primary table keeps all requests including failures.'}
    (a.root/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');(a.root/'scored-cases.json').write_text(json.dumps(scored,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
