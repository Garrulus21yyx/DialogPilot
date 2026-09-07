"""Resumable full-corpus BGE-M3 encoding, bound to source/model/query identity."""
import argparse
import gzip
import fcntl
from importlib.metadata import version
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import zipfile
import numpy as np
from FlagEmbedding import BGEM3FlagModel
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.adapt_mtrag_retrieval_dataset import digest,DOMAINS
from scripts.run_mtrag_lexical_query_pair import metrics


def atomic_json(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    os.replace(tmp,path)


def blocks(path,domain,size):
    batch=[]
    with zipfile.ZipFile(path) as z,z.open(domain+'.jsonl') as f:
        for line in f:
            row=json.loads(line)
            if not row['text'].strip():continue
            batch.append((f"mtrag:{domain}:{row['_id']}",row['text']))
            if len(batch)==size:yield batch;batch=[]
    if batch:yield batch


def merge_ranked(previous, ids, values, limit=100):
    top=sorted(range(len(ids)),key=lambda j:(-float(values[j]),ids[j]))[:limit]
    return sorted(previous+[(float(values[j]),ids[j]) for j in top],key=lambda x:(-x[0],x[1]))[:limit]


def run(a):
    a.output.mkdir(parents=True,exist_ok=True)
    source=json.loads(a.manifest.read_text())
    with gzip.open(a.lexical/'results.json.gz','rt') as f:
        lexical=[r for r in json.load(f) if r['mode']=='rewrite']
    model_files={p.name:digest(p) for p in a.model.iterdir() if p.is_file() and p.suffix in ('.json','.bin','.model','.safetensors')}
    identity={'model_files':model_files,'source_manifest_sha256':digest(a.manifest),
              'query_results_sha256':digest(a.lexical/'results.json.gz'),'max_length':8192,'batch_size':4,
              'representation':'official passage text only','dtype':'float32 normalized output / fp16 inference',
              'shard_size':4096,'libraries':{name:version(name) for name in ('FlagEmbedding','torch','transformers','numpy')},'encoder':'FlagEmbedding.BGEM3FlagModel','api_calls':0}
    identity_path=a.output/'identity.json'
    if identity_path.exists():
        if json.loads(identity_path.read_text())!=identity:raise ValueError('cache identity mismatch')
    else:atomic_json(identity_path,identity)
    model=BGEM3FlagModel(str(a.model),use_fp16=True,devices='cuda:0',return_dense=True,return_sparse=False,return_colbert_vecs=False)
    progress_path=a.output/'progress.json'
    started=time.monotonic();results=[];domain_reports={};encoded_total=0;cached_total=0
    for domain in DOMAINS:
        archive=a.corpora/f'{domain}.jsonl.zip'
        if digest(archive)!=source['source']['archives'][domain]['sha256']:raise ValueError('corpus checksum mismatch')
        qs=[r for r in lexical if r['domain']==domain]
        qtexts=[r['query'] for r in qs]
        if any(len(x)>8192 for x in model.tokenizer(qtexts,truncation=False)['input_ids']):raise ValueError('query exceeds model input')
        qvec=np.asarray(model.encode(qtexts,batch_size=4,max_length=8192,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs'],dtype=np.float32)
        best=[[] for _ in qs];total=0;maximum=0
        for bi,batch in enumerate(blocks(archive,domain,4096)):
            ids=[p[0] for p in batch];texts=[p[1] for p in batch]
            text_hash=hashlib.sha256(json.dumps(batch,ensure_ascii=False).encode()).hexdigest()
            name=f'{domain}-{bi:04d}';meta_path=a.output/(name+'.json');vector_path=a.output/(name+'.npy')
            if meta_path.exists():
                meta=json.loads(meta_path.read_text())
                if meta['input_sha256']!=text_hash or digest(vector_path)!=meta['vectors_sha256']:
                    raise ValueError('shard content mismatch')
                vec=np.load(vector_path);cached_total+=len(batch)
            else:
                lengths=[len(x) for x in model.tokenizer(texts,truncation=False)['input_ids']]
                if max(lengths)>8192:raise ValueError(f'passage exceeds8192 in {name}')
                ts=time.monotonic()
                vec=np.asarray(model.encode(texts,batch_size=4,max_length=8192,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs'],dtype=np.float32)
                if vec.shape!=(len(batch),1024) or not np.isfinite(vec).all():raise ValueError('invalid vectors')
                np.save(vector_path,vec)
                meta={'input_sha256':text_hash,'vectors_sha256':digest(vector_path),'rows':len(batch),
                      'max_tokens':max(lengths),'encoding_seconds':time.monotonic()-ts}
                atomic_json(meta_path,meta);encoded_total+=len(batch)
            maximum=max(maximum,meta['max_tokens']);total+=len(batch)
            scores=qvec@vec.T
            for qi,values in enumerate(scores):
                best[qi]=merge_ranked(best[qi],ids,values)
            atomic_json(progress_path,{'status':'RUNNING','domain':domain,'completed_domain_rows':total,
                'newly_encoded':encoded_total,'reused':cached_total,'elapsed_seconds':time.monotonic()-started,
                'latest_shard':name,'pid':os.getpid()})
            print(f'{domain} {total} passages; encoded={encoded_total} cached={cached_total}',flush=True)
        if total!=source['source']['stats'][domain]['passages']:raise ValueError('full corpus count mismatch')
        domain_reports[domain]={'passages':total,'max_tokens':maximum}
        for q,ranking in zip(qs,best):
            results.append({'case_id':q['case_id'],'group_id':q['group_id'],'domain':domain,'query':q['query'],
                'gold':q['gold'],'ranking':[{'id':pid,'score':score} for score,pid in ranking],
                'metrics':metrics([pid for _,pid in ranking[:20]],set(q['gold']))})
        with gzip.open(a.output/f'{domain}-results.json.gz','wt') as f:json.dump(results[-len(qs):],f)
    with gzip.open(a.output/'results.json.gz','wt') as f:json.dump(results,f)
    summary={k:sum(r['metrics'][k] for r in results)/len(results) for k in results[0]['metrics']}
    atomic_json(a.output/'report.json',{'scope':'32 frozen rewrite queries; exact dense over full separate domains; no ANN/rerank/generation','cases':len(results),'summary':summary,'domains':domain_reports,'api_calls':0,'newly_encoded':encoded_total,'reused':cached_total})
    atomic_json(progress_path,{'status':'COMPLETE','newly_encoded':encoded_total,'reused':cached_total,'elapsed_seconds':time.monotonic()-started,'pid':os.getpid()})
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('corpora','model','manifest','lexical','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    with (a.output/'run.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            run(a)
        except Exception as exc:
            atomic_json(a.output/'failure.json',{'status':'FAILED','error_type':type(exc).__name__,'error':str(exc),'pid':os.getpid()})
            raise
