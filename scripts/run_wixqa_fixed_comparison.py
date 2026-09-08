"""Frozen WixQA dev comparison; offline full-corpus retrieval, no external API."""
import argparse, gc, gzip, hashlib, json, math
from pathlib import Path
from statistics import mean
import numpy as np
import torch
from scripts.prepare_wixqa_local_index import digest
from scripts.run_mtrag_lexical_query_pair import stream_bm25
from infrastructure.bge_m3_embedding import LocalBGEM3EmbeddingProvider, BGEM3EmbeddingConfig
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from application.knowledge_retrieval_text import build_child_retrieval_text
from mcp.rank_fusion import fuse_rankings
from mcp.context_packer import ContextCandidate, ContextPacker


def measure(ids, source, gold, k):
    docs=list(dict.fromkeys(source[i]['source_id'] for i in ids))
    flags=[d in gold for d in docs]
    return {'article_recall':len(set(docs)&gold)/len(gold),
            'all_articles':gold.issubset(docs),
            'article_mrr':next((1/(i+1) for i,v in enumerate(flags) if v),0),
            'article_ndcg':sum(v/math.log2(i+2) for i,v in enumerate(flags))/sum(1/math.log2(i+2) for i in range(min(k,len(gold))))}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--split', choices=('dev','heldout'), default='dev')
    args=parser.parse_args()
    cache=Path('/tmp/dialogpilot-wixqa-full-index-20260908')
    output=Path(f'artifacts/eval/wixqa-fixed-{args.split}20-2026-09-08')
    manifest_path=Path('artifacts/eval/wixqa-connected-comparison-2026-09-08/manifest.json')
    manifest=json.loads(manifest_path.read_text())
    complete=json.loads((cache/'COMPLETE.json').read_text())
    assert digest(cache/'chunks.json.gz')==complete['chunks_sha256']
    assert digest(cache/'identity.json')==complete['identity_sha256']
    identity=json.loads((cache/'identity.json').read_text())
    for name,sha in identity['code_sha256'].items():assert digest(name)==sha
    chunks=json.loads(gzip.decompress((cache/'chunks.json.gz').read_bytes()))
    texts=[build_child_retrieval_text(title=c['title'],section_path=(),content=c['text']) for c in chunks]
    vectors=[]
    for start in range(0,len(chunks),128):
        p=cache/f'vectors-{start:06d}.npy';meta=json.loads(p.with_suffix('.json').read_text())
        assert digest(p)==meta['vector_sha256']
        assert hashlib.sha256(json.dumps(texts[start:start+128],ensure_ascii=False).encode()).hexdigest()==meta['input_sha256']
        v=np.load(p,allow_pickle=False)
        assert v.shape==(len(chunks[start:start+128]),1024) and np.isfinite(v).all()
        assert np.allclose(np.linalg.norm(v,axis=1),1,atol=1e-4)
        vectors.append(v)
    matrix=np.concatenate(vectors)
    lock=json.loads(Path('artifacts/eval/rag-three-dataset-lock-v3-2026-09-07/wixqa-test-lock.json').read_text())
    raw=Path('/tmp/dialogpilot-rag-external-lock-20260907');qas={}
    for config in {s['config'] for s in manifest['selected'][args.split]}:
        p=raw/f'{config}.jsonl';assert digest(p)==lock['source_files'][p.name]['sha256']
        qas[config]=p.read_text().splitlines()
    cases=[]
    for s in manifest['selected'][args.split]:
        q=json.loads(qas[s['config']][s['row_index']]);assert set(q['article_ids'])==set(s['article_ids'])
        cases.append({**s,'query':q['question']})
    source={c['id']:c for c in chunks};ids=list(source)
    assert all(set(c['article_ids'])<= {d['source_id'] for d in chunks} for c in cases)
    output.mkdir(exist_ok=False)
    model_path=Path('/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots')/identity['model_revision']
    for name,sha in identity['model_files'].items():assert digest(model_path/name)==sha
    provider=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(model_path,model_path.name,identity['model_files']['pytorch_model.bin'],device='cuda',batch_size=16))
    queries=[c['query'] for c in cases]
    assert max(map(len,provider._model.tokenizer(queries,truncation=False)['input_ids']))<=provider._model.max_seq_length
    qvectors=np.asarray(provider.embed_queries(queries),dtype=np.float32)
    np.save(output/'query-vectors.npy',qvectors)
    dense=qvectors@matrix.T
    del provider;gc.collect();torch.cuda.empty_cache()
    lexical_ids,bm25=stream_bm25(queries,zip(ids,texts,strict=True));assert lexical_ids==ids
    print('verified cache and scored both routes',flush=True)
    reranker=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4)
    (output/'identity.json').write_text(json.dumps({'index':complete,'selection_sha256':digest(manifest_path),'reranker':reranker.identity,'script_sha256':digest(__file__),'scope':__doc__},indent=2)+'\n')
    results=[];pairs=0
    for qi,case in enumerate(cases):
        ranks={'dense':sorted(range(len(ids)),key=lambda j:(-float(dense[qi,j]),ids[j]))[:20],
               'bm25':sorted((j for j in range(len(ids)) if bm25[qi,j]>0),key=lambda j:(-float(bm25[qi,j]),ids[j]))[:20]}
        ranks={r:[ids[j] for j in js] for r,js in ranks.items()}
        arms={str(a):fuse_rankings(ranks,weights={'dense':a,'bm25':1-a},rrf_k=10,top_k=20) for a in (.25,.5)}
        union=list(dict.fromkeys(i for arm in arms.values() for i in arm))
        scores=reranker._score(case['query'],[build_child_retrieval_text(title=source[i]['title'],section_path=(),content=source[i]['text']) for i in union])
        assert len(scores)==len(union) and all(math.isfinite(s) for s in scores)
        score=dict(zip(union,scores,strict=True));pairs+=len(union);gold=set(case['article_ids']);out={}
        for arm,candidate_ids in arms.items():
            ordered=sorted(candidate_ids,key=lambda i:(-score[i],candidate_ids.index(i)))
            candidates=[ContextCandidate(chunk_id=i,document_id=source[i]['source_id'],text=source[i]['text'],start_char=source[i]['start_char'],end_char=source[i]['end_char'],title=source[i]['title'],source_checksum=source[i]['source_checksum'],source_revision=manifest['dataset_revision']) for i in ordered]
            pack=ContextPacker().pack(candidates,max_tokens=2600,max_chunks=5)
            out[arm]={'candidate20':measure(candidate_ids,source,gold,20),'ce5':measure(ordered[:5],source,gold,5),'pack5':measure(pack.chunk_ids,source,gold,5),'candidate_ids':candidate_ids,'ce_order':ordered,'packed_ids':list(pack.chunk_ids),'skipped_budget':list(pack.skipped_budget)}
        result={'case':case,'routes':ranks,'scores':score,'arms':out};results.append(result)
        with (output/'cases.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
        print(qi+1,'/20',out['0.25']['pack5']['article_recall'],out['0.5']['pack5']['article_recall'],flush=True)
    stages=('candidate20','ce5','pack5')
    summary={a:{s:{k:mean(r['arms'][a][s][k] for r in results) for k in results[0]['arms'][a][s]} for s in stages} for a in arms}
    paired={s:{'better':sum(r['arms']['0.5'][s]['article_recall']>r['arms']['0.25'][s]['article_recall'] for r in results),'worse':sum(r['arms']['0.5'][s]['article_recall']<r['arms']['0.25'][s]['article_recall'] for r in results)} for s in stages}
    report={'split':args.split,'cases':len(results),'corpus_articles':6221,'chunks':len(chunks),'api_calls':0,'unique_ce_pairs':pairs,'summary':summary,'paired':paired,'metric_scope':'Article qrels; first deduplicate sources within the fixed chunk budget; nDCG ideal uses stage chunk budget. Not span recall, actual PG latency, Agent queries, or answer correctness.'}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (output/'cases.jsonl.gz').write_bytes(gzip.compress((output/'cases.jsonl').read_bytes(),mtime=0))
    print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':main()
