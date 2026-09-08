"""Local-only screening of fixed .25 vs .5 fusion on frozen ecommerce routes."""
import gzip,json,hashlib,math
from pathlib import Path
from statistics import mean
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from application.knowledge_retrieval_text import build_child_retrieval_text
from evaluation.rag_ecommerce_dev import synthetic_development
from mcp.context_packer import ContextCandidate,ContextPacker
from mcp.rank_fusion import fuse_rankings


def measure(ids,source,case):
    hits=[any(source[i]['source_id']==e.document_id and source[i]['source_start_char']<=e.start_char and source[i]['source_end_char']>=e.end_char for i in ids) for e in case.evidence]
    gold={e.document_id for e in case.evidence};docs=list(dict.fromkeys(source[i]['source_id'] for i in ids))
    flags=[d in gold for d in docs];ideal=sum(1/math.log2(i+2) for i in range(min(5,len(gold))))
    return {'span_recall':mean(hits),'all_spans':all(hits),'document_mrr':next((1/(i+1) for i,v in enumerate(flags) if v),0),'document_ndcg':sum(v/math.log2(i+2) for i,v in enumerate(flags))/ideal}


def main():
    path=Path('artifacts/eval/rag-route-capture-combined-2026-09-08/source-route-captures.json.gz')
    captured=json.loads(gzip.decompress(path.read_bytes()));cases=synthetic_development()[1]
    root=Path('artifacts/eval/rag-balanced-local20-2026-09-08');root.mkdir(exist_ok=False)
    model=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4)
    (root/'identity.json').write_text(json.dumps({'model':model.identity,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'scope':__doc__},indent=2)+'\n')
    results=[];pairs=0
    for index,case in enumerate(cases):
        row=captured[2*index];source=row['source_union']
        rankings={route:tuple(sorted(rs,key=lambda i:(rs[i],i))) for route,rs in row['route_ranks'].items()}
        arms={str(a):fuse_rankings(rankings,weights={'raw:vector':a,'raw:bm25':1-a},rrf_k=10,top_k=20) for a in (.25,.5)}
        assert arms['0.25']==[x['chunk_id'] for x in row['fused_candidates']]
        union=list(dict.fromkeys(i for ids in arms.values() for i in ids))
        texts=[build_child_retrieval_text(title=source[i]['title'] or i,section_path=(),content=source[i]['content']) for i in union]
        scores=model._score(row['query'],texts);assert len(scores)==len(union) and all(math.isfinite(s) for s in scores)
        score=dict(zip(union,scores,strict=True));pairs+=len(scores)
        output={}
        for arm,ids in arms.items():
            ordered=sorted(ids,key=lambda i:(-score[i],ids.index(i)))
            candidates=[ContextCandidate(chunk_id=i,document_id=source[i]['source_id'],text=source[i]['content'],start_char=source[i]['source_start_char'],end_char=source[i]['source_end_char'],title=source[i]['title'],source_checksum=source[i]['source_checksum'],source_revision=source[i]['source_revision']) for i in ordered]
            pack=ContextPacker().pack(candidates,max_tokens=2600,max_chunks=5)
            output[arm]={'ce5':measure(ordered[:5],source,case),'pack5':measure(pack.chunk_ids,source,case),'ce_order':ordered,'packed_ids':list(pack.chunk_ids),'skipped_budget':list(pack.skipped_budget)}
        result={'case_id':case.case_id,'query':row['query'],'scores':score,'arms':output};results.append(result)
        with (root/'cases.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
        print(case.case_id,'scored',len(union),flush=True)
    summary={a:{stage:{k:mean(r['arms'][a][stage][k] for r in results) for k in results[0]['arms'][a][stage]} for stage in ('ce5','pack5')} for a in arms}
    paired={stage:{'better':sum(r['arms']['0.5'][stage]['span_recall']>r['arms']['0.25'][stage]['span_recall'] for r in results),'worse':sum(r['arms']['0.5'][stage]['span_recall']<r['arms']['0.25'][stage]['span_recall'] for r in results)} for stage in ('ce5','pack5')}
    (root/'report.json').write_text(json.dumps({'cases':len(results),'api_calls':0,'unique_pairs':pairs,'summary':summary,'paired':paired,'limits':'Synthetic development, local reranker; not Flash or final answer quality.'},indent=2)+'\n')
    (root/'cases.jsonl.gz').write_bytes(gzip.compress((root/'cases.jsonl').read_bytes(),mtime=0))
    print(json.dumps({'summary':summary,'paired':paired,'unique_pairs':pairs},indent=2))

if __name__=='__main__':main()
