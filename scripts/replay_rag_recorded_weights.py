"""Frozen synthetic development replay; no provider or re-embedding calls."""
import gzip,json,hashlib,math
from pathlib import Path
from statistics import mean
from evaluation.rag_ecommerce_dev import synthetic_development
from mcp.rank_fusion import fuse_rankings


def main():
    root=Path('artifacts/eval/rag-route-capture-combined-2026-09-08')
    manifest=json.loads((root/'manifest.json').read_text())
    raw=(root/'source-route-captures.json.gz').read_bytes();records=json.loads(gzip.decompress(raw))
    docs,cases=synthetic_development();source_docs={(d['document_id'],hashlib.sha256(d['content'].encode()).hexdigest()):d['content'] for d in manifest['source_documents']}
    assert all(source_docs.get((d.document_id,hashlib.sha256(d.content.encode()).hexdigest()))==d.content for d in docs)
    assert len(records)==2*len(cases)
    results=[]
    for index,case in enumerate(cases):
        r=records[index*2]
        expected=manifest['fixed_query_override'].get(case.case_id,case.query)
        assert r['query']==expected==records[index*2+1]['query']
        assert r['projection_complete'] and r['selected_projection_matches']
        assert r['rrf_k']==10 and r['top_k']==20 and set(r['route_ranks'])=={'raw:vector','raw:bm25'}
        union=r['source_union']
        for c in union.values():
            # Source identity is authoritative; unrelated same-text documents
            # must not turn a miss into a hit.
            assert source_docs[(c['source_id'],c['source_checksum'])][c['source_start_char']:c['source_end_char']]==c['content']
        rankings={route:tuple(sorted(rs,key=lambda i:(rs[i],i))) for route,rs in r['route_ranks'].items()}
        gold_docs={e.document_id for e in case.evidence}
        arms={}
        for alpha in (0,.25,.5,.75,1):
            ids=fuse_rankings(rankings,weights={'raw:vector':alpha,'raw:bm25':1-alpha},rrf_k=10,top_k=20)
            if alpha==.25: assert ids==[c['chunk_id'] for c in r['fused_candidates']]
            hits=[any(union[i]['source_id']==e.document_id and union[i]['source_start_char']<=e.start_char and union[i]['source_end_char']>=e.end_char for i in ids) for e in case.evidence]
            ranked_docs=list(dict.fromkeys(union[i]['source_id'] for i in ids))
            flags=[d in gold_docs for d in ranked_docs[:20]]
            dcg=sum(hit/math.log2(rank+2) for rank,hit in enumerate(flags))
            ideal=sum(1/math.log2(rank+2) for rank in range(min(20,len(gold_docs))))
            arms[str(alpha)]={'span_recall_at_chunk20':mean(hits),'all_spans_at_chunk20':all(hits),
                 'document_mrr_at20':next((1/(rank+1) for rank,hit in enumerate(flags) if hit),0),
                 'document_ndcg_at20':dcg/ideal if ideal else None}
        results.append({'case_id':case.case_id,'arms':arms})
    summary={a:{k:mean(r['arms'][a][k] for r in results) for k in results[0]['arms'][a]} for a in results[0]['arms']}
    report={'scope':__doc__,'api_calls':0,'cases':len(cases),'source_sha256':hashlib.sha256(raw).hexdigest(),
            'metric_units':'Span coverage uses top20 chunks. Binary document MRR/nDCG uses first-appearance deduplicated source documents from that fixed chunk budget. Neither is final answer quality.',
            'summary':summary,'rows':results}
    out=Path('artifacts/eval/rag-recorded-weight-replay-2026-09-08');out.mkdir(exist_ok=True)
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
