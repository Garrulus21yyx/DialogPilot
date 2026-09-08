"""Frozen unseen-locally 100-per-dataset retrieval acceptance; local models only."""
import argparse,gzip,json,hashlib,gc,time,zipfile
from pathlib import Path
import numpy as np
from scripts.replay_rag_rank_selection import read,digest,pack
from scripts.run_mtrag_lexical_query_pair import stream_bm25
from scripts.run_mtrag_dense_shards import blocks
from scripts.run_mtrag_reranker_pair import at5
from scripts.run_wixqa_fixed_comparison import measure
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection,ranked,complete,sha
from evaluation.local_bge_m3_retrieval_eval import bm25_matrix
from application.knowledge_retrieval_text import build_child_retrieval_text
from mcp.rank_fusion import fuse_rankings
from mcp.context_packer import ContextCandidate
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
ROOT=Path('artifacts/eval/rag-fresh100-2026-09-08')
MODEL=Path('/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181')
def release(model):
    import torch
    del model;gc.collect();torch.cuda.empty_cache()
def route_rows(queries,ids,dense,lex):
    return [{'dense':list(ranked(dense[i],ids,20)),'bm25':[ids[j] for j in sorted(range(len(ids)),key=lambda j:(-float(lex[i,j]),ids[j])) if lex[i,j]>0][:20]} for i in range(len(queries))]
def source(cid,did,text,start,title='',checksum=''):
    return ContextCandidate(cid,did,text,start,start+len(text),title=title,source_checksum=checksum or hashlib.sha256(text.encode()).hexdigest(),source_revision='frozen-source-v1',index_manifest_fingerprint='fresh100-frozen')
def doc():
    from sentence_transformers import SentenceTransformer
    ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True);cases=ds.cases;_,chunks,texts=projection(ds.documents,cases,512,64,'structure_aware');ids=[c.chunk_id for c in chunks];byid=dict(zip(ids,chunks));docs={d.document_id:d for d in ds.documents}
    model=SentenceTransformer(str(MODEL),device='cuda',local_files_only=True)
    vectors=model.encode(texts,batch_size=16,normalize_embeddings=True,convert_to_numpy=True);qvec=model.encode([c.query for c in cases],batch_size=16,normalize_embeddings=True,convert_to_numpy=True)
    release(model);routes=route_rows(cases,ids,qvec@vectors.T,bm25_matrix([c.query for c in cases],texts))
    src={cid:source(cid,c.document_id,c.content,c.start_char,docs[c.document_id].title,hashlib.sha256(docs[c.document_id].content.encode()).hexdigest()) for cid,c in byid.items()}
    assert all(c.content==docs[c.document_id].content[c.start_char:c.end_char] for c in chunks)
    hitmap={cid:dict(document_id=c.document_id,source_start_char=c.start_char,source_end_char=c.end_char) for cid,c in byid.items()}
    rows=[{'id':c.case_id,'group':c.group_id,'query':c.query,'routes':r,'case':c} for c,r in zip(cases,routes,strict=True)]
    def metric(row,selected):
        m=evaluate_ranked_hits(row['case'],selected,hitmap,top_k=5);return {'recall':float(complete(row['case'],selected,byid)),'mrr':m['mrr'],'ndcg':m['ndcg']}
    return rows,src,dict(zip(ids,texts)),metric,{'documents':len(docs),'chunks':len(chunks),'new_document_embeddings':len(chunks),'embedding':'SentenceTransformer normalized BGE-M3 matching Doc dev recipe'}
def wix():
    from infrastructure.bge_m3_embedding import LocalBGEM3EmbeddingProvider,BGEM3EmbeddingConfig
    cache=Path('/tmp/dialogpilot-wixqa-full-index-20260908');ident=read(cache/'identity.json');finish=read(cache/'COMPLETE.json');assert digest(cache/'chunks.json.gz')==finish['chunks_sha256'];chunks=read(cache/'chunks.json.gz');ids=[c['id'] for c in chunks];byid=dict(zip(ids,chunks));texts=[build_child_retrieval_text(title=c['title'],section_path=(),content=c['text']) for c in chunks]
    arrays=[]
    for start in range(0,len(ids),128):
        p=cache/f'vectors-{start:06d}.npy';meta=read(p.with_suffix('.json'));assert digest(p)==meta['vector_sha256'];assert hashlib.sha256(json.dumps(texts[start:start+128],ensure_ascii=False).encode()).hexdigest()==meta['input_sha256'];arrays.append(np.load(p))
    cases=read(ROOT/'selection.json')['WixQA'];model=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(MODEL,MODEL.name,ident['model_files']['pytorch_model.bin'],device='cuda',batch_size=16));queries=[c['query'] for c in cases];qvec=np.asarray(model.embed_queries(queries),dtype=np.float32);release(model)
    lid,lex=stream_bm25(queries,zip(ids,texts,strict=True));assert lid==ids
    routes=route_rows(queries,ids,qvec@np.concatenate(arrays).T,lex);src={cid:source(cid,c['source_id'],c['text'],c['start_char'],c['title'],c['source_checksum']) for cid,c in byid.items()}
    rows=[{'id':c['id'],'group':c['group_id'],'query':c['query'],'routes':r,'gold':set(c['article_ids'])} for c,r in zip(cases,routes,strict=True)]
    def metric(row,selected):
        m=measure(selected,byid,row['gold'],5);return {'recall':m['article_recall'],'mrr':m['article_mrr'],'ndcg':m['article_ndcg']}
    return rows,src,dict(zip(ids,texts)),metric,{'articles':6221,'chunks':len(ids),'new_document_embeddings':0}
def mtrag():
    from FlagEmbedding import BGEM3FlagModel
    cases=read(ROOT/'selection.json')['MTRAG'];manifest=read('/tmp/dialogpilot-mtrag-adapted-v2-20260907/manifest.json');cache=Path('/tmp/dialogpilot-mtrag-dense-full-20260907');ident=read(cache/'identity.json')
    for name,h in ident['model_files'].items():assert digest(MODEL/name)==h
    model=BGEM3FlagModel(str(MODEL),use_fp16=True,devices='cuda:0');qvec=np.asarray(model.encode([c['query'] for c in cases],batch_size=4,max_length=8192,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs'],dtype=np.float32);release(model)
    rows=[];src={};texts={};counts={}
    for domain in ('clapnq','cloud','fiqa','govt'):
        selected=[(i,c) for i,c in enumerate(cases) if domain in c['query_types']]
        if not selected:continue
        p=Path('/tmp/dialogpilot-mtrag-corpora-20260907')/(domain+'.jsonl.zip');assert digest(p)==manifest['source']['archives'][domain]['sha256'];ids=[];tt=[];dense=[];titles={}
        for bi,batch in enumerate(blocks(p,domain,4096)):
            vp=cache/f'{domain}-{bi:04d}.npy';meta=read(vp.with_suffix('.json'));assert digest(vp)==meta['vectors_sha256'];assert hashlib.sha256(json.dumps(batch,ensure_ascii=False).encode()).hexdigest()==meta['input_sha256']
            v=np.load(vp);dense.append(qvec[[i for i,c in selected]]@v.T);ids.extend(x[0] for x in batch);tt.extend(x[1] for x in batch)
        assert len(ids)==manifest['source']['stats'][domain]['passages'];counts[domain]=len(ids)
        lid,lex=stream_bm25([c['query'] for _,c in selected],zip(ids,tt,strict=True));assert lid==ids
        routes=route_rows(selected,ids,np.concatenate(dense,axis=1),lex);wanted={cid for r in routes for rr in r.values() for cid in rr}
        with zipfile.ZipFile(p) as z,z.open(domain+'.jsonl') as f:
            for line in f:
                d=json.loads(line);cid='mtrag:'+domain+':'+d['_id']
                if cid in wanted:titles[cid]=d.get('title') or ''
        for cid,text in zip(ids,tt,strict=True):
            if cid in wanted:src[cid]=source(cid,cid,text,0,titles[cid]);texts[cid]=text
        rows.extend({'id':c['id'],'group':c['group_id'],'query':c['query'],'routes':r,'gold':{e['document_id'] for e in c['evidence']}} for (_,c),r in zip(selected,routes,strict=True));print('MTRAG full domain',domain,len(ids),flush=True)
    def metric(row,selected):
        m=at5(selected,row['gold']);return {'recall':m['recall@5'],'mrr':m['mrr@5'],'ndcg':m['ndcg@5']}
    return rows,src,texts,metric,{'domain_passages':counts,'new_document_embeddings':0}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('dataset',choices=['Doc2Dial','MTRAG','WixQA']);args=parser.parse_args();name=args.dataset
    out=ROOT/name;out.mkdir(exist_ok=False);(out/'manifest.json').write_text(json.dumps({'selection_sha256':digest(ROOT/'selection.json'),'script_sha256':digest(__file__),'api_calls':0,'frozen_strategy':read(ROOT/'selection.json')['strategies'][name]},indent=2)+'\n')
    start=time.monotonic();rows,src,texts,metric,identity={'Doc2Dial':doc,'MTRAG':mtrag,'WixQA':wix}[name]();assert len(rows)==100
    scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4)
    (out/'identity.json').write_text(json.dumps({'corpus':identity,'reranker':scorer.identity},indent=2)+'\n');results=[];scored=[];pairs=0
    for i,row in enumerate(rows):
        pool=list(fuse_rankings(row['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20 if name=='MTRAG' else 40));scores=scorer._score(row['query'],[texts[cid] for cid in pool]);lookup=dict(zip(pool,scores,strict=True));pairs+=len(pool);assert pairs<=4000
        scored.extend({'case_id':row['id'],'cid':cid,'query':row['query'],'text_sha256':hashlib.sha256(texts[cid].encode()).hexdigest(),'score':s} for cid,s in lookup.items())
        base=pool[:20];tie=(lambda cid:pool.index(cid)) if name=='WixQA' else (lambda cid:cid)
        baseorder=sorted(base,key=lambda cid:(-lookup[cid],tie(cid)));ce=sorted(pool,key=lambda cid:(-lookup[cid],tie(cid)))
        final=ce if name=='WixQA' else list(fuse_rankings({'ce':ce,'recall':pool},weights={'ce':.75,'recall':.25},rrf_k=10,top_k=len(pool)))
        arms={}
        for arm,order in [('baseline',baseorder),('candidate',final)]:
            selected,tokens=pack(row['query'],order,src);arms[arm]={**metric(row,selected),'ids':selected,'tokens':tokens}
        results.append({'id':row['id'],'group':row['group'],'query':row['query'],'routes':row['routes'],'pool':pool,'arms':arms})
        if i%20==0:print(name,i,'pairs',pairs,flush=True)
    for f,data in [('cases',results),('scores',scored)]: (out/(f+'.json.gz')).write_bytes(gzip.compress(json.dumps(data).encode(),mtime=0))
    report={'n':100,'groups':len({r['group'] for r in results}),'api_calls':0,'ce_pairs':pairs,'wall_seconds':time.monotonic()-start,'summary':{a:{k:sum(r['arms'][a][k] for r in results)/100 for k in ('recall','mrr','ndcg')} for a in ('baseline','candidate')},'paired':{'better':sum(r['arms']['candidate']['recall']>r['arms']['baseline']['recall'] for r in results),'worse':sum(r['arms']['candidate']['recall']<r['arms']['baseline']['recall'] for r in results)}}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(name,report,flush=True)
if __name__=='__main__':main()
