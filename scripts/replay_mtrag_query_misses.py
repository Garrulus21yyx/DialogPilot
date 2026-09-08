"""Frozen context rewrites against full corpus; reuse audited passage vectors."""
import argparse,gzip,json,re,time,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.adapt_mtrag_retrieval_dataset import digest
from scripts.run_mtrag_dense_shards import blocks
from scripts.run_mtrag_lexical_query_pair import stream_bm25,metrics
from mcp.rank_fusion import fuse_rankings


def run(a):
    import torch
    from FlagEmbedding import BGEM3FlagModel
    manual=json.loads(a.queries.read_text())['cases']
    if not a.frozen:
        with gzip.open(a.baseline/'results.json.gz','rt') as f:base={r['case_id']:r for r in json.load(f)}
    identity=json.loads((a.cache/'identity.json').read_text())
    if json.loads((a.cache/'progress.json').read_text())['status']!='COMPLETE':raise ValueError('incomplete cache')
    manifest=json.loads(a.manifest.read_text())
    if identity['source_manifest_sha256']!=digest(a.manifest):raise ValueError('source identity mismatch')
    for name,sha in identity['model_files'].items():
        if digest(a.model/name)!=sha:raise ValueError('embedding model mismatch')
    if a.frozen:
        variants=[{**r,'mode':'official'} for r in manual]
    else:
        variants=[]
        for r in manual:
            b=base[r['case_id']]
            for mode,q in [('official',b['query']),('role_stripped',re.sub(r'^\|user\|:\s*','',b['query'])),('context_authored',r['authored_query'])]:
                variants.append({'case_id':r['case_id'],'domain':b['domain'],'mode':mode,'query':q,'gold':b['gold']})
    a.output.mkdir(parents=True,exist_ok=False)
    model=BGEM3FlagModel(str(a.model),use_fp16=True,devices='cuda:0',return_dense=True,return_sparse=False,return_colbert_vecs=False)
    texts=[v['query'] for v in variants]
    if any(len(x)>8192 for x in model.tokenizer(texts,truncation=False)['input_ids']):raise ValueError('query too long')
    qvec=np.asarray(model.encode(texts,batch_size=4,max_length=8192,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs'],dtype=np.float32)
    del model;torch.cuda.empty_cache()
    np.save(a.output/'query-vectors.npy',qvec)
    results=[]
    for domain in sorted({v['domain'] for v in variants}):
        ix=[i for i,v in enumerate(variants) if v['domain']==domain]
        archive=a.corpora/f'{domain}.jsonl.zip'
        if digest(archive)!=manifest['source']['archives'][domain]['sha256']:raise ValueError('archive mismatch')
        def docs():
            for batch in blocks(archive,domain,4096):yield from batch
        ids,lex=stream_bm25([variants[i]['query'] for i in ix],docs())
        dense=np.empty((len(ix),len(ids)),dtype=np.float32);offset=0
        for bi,batch in enumerate(blocks(archive,domain,4096)):
            p=a.cache/f'{domain}-{bi:04d}.npy';meta=json.loads(p.with_suffix('.json').read_text())
            if digest(p)!=meta['vectors_sha256']:raise ValueError('vectors changed')
            vec=np.load(p);n=len(batch)
            if [pid for pid,_ in batch]!=ids[offset:offset+n] or len(vec)!=n:raise ValueError('row alignment mismatch')
            dense[:,offset:offset+n]=qvec[ix]@vec.T;offset+=n
        if offset!=len(ids):raise ValueError('incomplete scores')
        for local,i in enumerate(ix):
            v=variants[i];gold=set(v['gold']);routes={};diagnostic={}
            for name,values in [('dense',dense[local]),('bm25',lex[local])]:
                eligible=range(len(ids)) if name=='dense' else np.flatnonzero(values>0)
                order=sorted(eligible,key=lambda j:(-float(values[j]),ids[j]))
                positions={ids[j]:rank+1 for rank,j in enumerate(order) if ids[j] in gold}
                routes[name]=[ids[j] for j in order[:20]]
                diagnostic[name]={'gold_ranks':{g:positions.get(g) for g in sorted(gold)},'metrics':metrics(routes[name],gold)}
            fusion={}
            for alpha in ((.25,.75) if a.frozen else (.25,.5,.75)):
                rank=fuse_rankings(routes,weights={'dense':alpha,'bm25':1-alpha},rrf_k=10,top_k=20)
                fusion[str(alpha)]={'ranking':rank,'metrics':metrics(rank,gold)}
            results.append({**v,'routes':routes,'diagnostic':diagnostic,'fusion':fusion})
    with gzip.open(a.output/'cases.json.gz','wt') as f:json.dump(results,f,ensure_ascii=False)
    report={'scope':'frozen query retrieval; no rerank/answers' if a.frozen else '4 exposed failures, same full corpus, query-only diagnosis; no rerank/answers','api_calls':0,'query_embeddings':len(variants),'document_embeddings':0,
            'queries_sha256':digest(a.queries),'cache_identity_sha256':digest(a.cache/'identity.json'),
            'summary':{mode:{name:sum(r['diagnostic'][name]['metrics']['recall@20'] for r in results if r['mode']==mode)/len(manual) for name in ('dense','bm25')} for mode in (('official',) if a.frozen else ('official','role_stripped','context_authored'))}}
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('queries','baseline','cache','manifest','model','corpora','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--frozen',action='store_true',help='score supplied queries once; fixed .25/.75 comparison')
    run(p.parse_args())
