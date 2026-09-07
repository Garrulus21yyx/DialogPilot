"""Read-only audit of committed dense shards against source text and cache identity."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.run_mtrag_dense_shards import blocks
from scripts.adapt_mtrag_retrieval_dataset import DOMAINS,digest


def check_vector_file(path,meta,expected_rows):
    if digest(path)!=meta['vectors_sha256']:raise ValueError('vector checksum mismatch')
    values=np.load(path,mmap_mode='r',allow_pickle=False)
    if values.shape!=(expected_rows,1024) or values.dtype!=np.float32:
        raise ValueError('vector shape or dtype mismatch')
    if not np.isfinite(values).all():raise ValueError('nonfinite vector')
    norms=np.linalg.norm(values,axis=1)
    if not np.allclose(norms,1,atol=.002):raise ValueError('vector normalization mismatch')
    return float(norms.min()),float(norms.max())


def audit(cache,corpora,manifest_path,require_complete=True):
    started=time.monotonic()
    manifest=json.loads(manifest_path.read_text())
    identity=json.loads((cache/'identity.json').read_text())
    if identity['source_manifest_sha256']!=digest(manifest_path):raise ValueError('source identity mismatch')
    if identity['shard_size']!=4096 or identity['max_length']!=8192:raise ValueError('unsupported encoding contract')
    progress=json.loads((cache/'progress.json').read_text())
    if require_complete and progress['status']!='COMPLETE':raise ValueError('encoding not complete')
    # Freeze only atomically committed metadata; vector files alone are not checkpoints.
    snapshot={domain:sorted(cache.glob(domain+'-[0-9][0-9][0-9][0-9].json')) for domain in DOMAINS}
    report={};total=0
    for domain in DOMAINS:
        paths=snapshot[domain]
        if [p.name for p in paths]!=[f'{domain}-{i:04d}.json' for i in range(len(paths))]:
            raise ValueError('non-contiguous committed shards')
        archive=corpora/(domain+'.jsonl.zip')
        if digest(archive)!=manifest['source']['archives'][domain]['sha256']:raise ValueError('archive identity mismatch')
        count=0;checks=[]
        for i,batch in enumerate(blocks(archive,domain,4096)):
            if i>=len(paths):break
            meta=json.loads(paths[i].read_text())
            text_hash=hashlib.sha256(json.dumps(batch,ensure_ascii=False).encode()).hexdigest()
            if meta['input_sha256']!=text_hash or meta['rows']!=len(batch):raise ValueError('source-to-vector row mismatch')
            if not 0<meta['max_tokens']<=8192:raise ValueError('invalid encoder visibility record')
            lo,hi=check_vector_file(paths[i].with_suffix('.npy'),meta,len(batch))
            count+=len(batch)
            checks.append({'file':paths[i].name,'rows':len(batch),'input_sha256':text_hash,
                          'vectors_sha256':meta['vectors_sha256'],'norm_min':lo,'norm_max':hi,'max_tokens':meta['max_tokens']})
        if len(checks)!=len(paths):raise ValueError('cache has extra source shards')
        expected=manifest['source']['stats'][domain]['passages']
        if count>expected or (require_complete and count!=expected):raise ValueError('incomplete full-domain coverage')
        report[domain]={'checked_rows':count,'expected_rows':expected,'shards':checks}
        total+=count
    return {'mode':'complete' if require_complete else 'committed_checkpoint_snapshot',
        'complete_attested':require_complete,'api_calls':0,'checked_rows':total,'domains':report,
        'identity_sha256':digest(cache/'identity.json'),'elapsed_seconds':time.monotonic()-started,
        'scope':'Input ordering/hash, vector bytes, shape, finite values, unit norm and recorded visibility. Does not independently prove embedding semantics or retrieval scores.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('cache','corpora','manifest','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--allow-running',action='store_true')
    a=p.parse_args();result=audit(a.cache,a.corpora,a.manifest,not a.allow_running)
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='domains'},indent=2))
