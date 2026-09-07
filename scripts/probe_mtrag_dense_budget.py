"""Gold-independent corpus sample for local encoder cost and truncation audit."""
import argparse
import hashlib
import heapq
import json
from pathlib import Path
import time
import zipfile
import numpy as np
import torch
from FlagEmbedding import BGEM3FlagModel

DOMAINS=('clapnq','cloud','fiqa','govt')


def sample(corpora,per_domain):
    selected=[];counts={}
    for domain in DOMAINS:
        heap=[];count=0
        with zipfile.ZipFile(corpora/f'{domain}.jsonl.zip') as z,z.open(domain+'.jsonl') as f:
            for line in f:
                r=json.loads(line)
                if not r['text'].strip():continue
                count+=1
                key=int(hashlib.sha256(('mtrag-dense-budget-v1\0'+domain+'\0'+r['_id']).encode()).hexdigest(),16)
                entry=(-key,r['_id'],r['text'])
                if len(heap)<per_domain:heapq.heappush(heap,entry)
                elif entry>heap[0]:heapq.heapreplace(heap,entry)
        counts[domain]=count
        selected.extend({'domain':domain,'id':pid,'text':text} for _,pid,text in sorted(heap,reverse=True))
    return selected,counts


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('corpora','model','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    data,counts=sample(a.corpora,64)
    model=BGEM3FlagModel(str(a.model),use_fp16=True,devices='cuda:0',return_dense=True,return_sparse=False,return_colbert_vecs=False)
    texts=[r['text'] for r in data]
    lengths=[len(x) for x in model.tokenizer(texts,truncation=False)['input_ids']]
    for r,n in zip(data,lengths):r['tokens']=n
    limits={str(k):{'truncated_samples':sum(n>k for n in lengths),'samples':len(lengths)} for k in (512,1024,8192)}
    model.encode(texts[:4],batch_size=4,max_length=8192,return_dense=True,return_sparse=False,return_colbert_vecs=False)
    torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
    start=time.monotonic()
    encoded=model.encode(texts,batch_size=4,max_length=8192,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs']
    torch.cuda.synchronize();elapsed=time.monotonic()-start
    assert np.isfinite(encoded).all() and encoded.shape==(256,1024)
    np.save(a.output/'sample-vectors.npy',encoded)
    report={'purpose':'cost/truncation probe only, no retrieval evaluation','api_calls':0,'sample_seed':'mtrag-dense-budget-v1','corpus_counts':counts,
        'samples':[{k:v for k,v in r.items() if k!='text'} for r in data], 'token_limits':limits,
        'token_percentiles':dict(zip(('p50','p95','max'),np.quantile(lengths,[.5,.95,1]).tolist())),
        'batch_size':4,'max_length':8192,'fp16':True,'encoding_seconds':elapsed,'passages_per_second':len(data)/elapsed,
        'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'model_path':str(a.model),
        'naive_full_corpus_seconds':sum(counts.values())/len(data)*elapsed,
        'extrapolation_warning':'sample estimate, batching/length distribution/I/O may change full-run cost',
        'sample_vectors_sha256':hashlib.sha256((a.output/'sample-vectors.npy').read_bytes()).hexdigest()}
    (a.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2))
