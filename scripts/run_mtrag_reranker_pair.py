"""Fixed three-arm candidate pools, shared local pointwise cross-encoder scores."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.adapt_mtrag_retrieval_dataset import digest,DOMAINS

ARMS=('0.25','0.5','0.75')


def at5(ids,gold):
    ids=ids[:5]
    return {'recall@5':len(set(ids)&gold)/len(gold),
            'mrr@5':next((1/(i+1) for i,d in enumerate(ids) if d in gold),0.),
            'ndcg@5':sum(1/math.log2(i+2) for i,d in enumerate(ids) if d in gold)/sum(1/math.log2(i+2) for i in range(min(5,len(gold))))}


def rerank(ids,scores):
    return sorted(ids,key=lambda p:(-scores[p],p))


def run(a):
    arms_to_score=tuple(a.arms) if getattr(a,"arms",None) else ARMS
    with gzip.open(a.hybrid/'cases.json.gz','rt') as f:cases=json.load(f)
    manifest=json.loads(a.manifest.read_text())
    needed=sorted({(i,p) for i,r in enumerate(cases) for arm in arms_to_score for p in r['variants'][arm]['ranking']})
    assert len(needed)<=20*len(cases)*len(arms_to_score)
    wanted={p for _,p in needed};texts={}
    for domain in DOMAINS:
        archive=a.corpora/f'{domain}.jsonl.zip'
        if digest(archive)!=manifest['source']['archives'][domain]['sha256']:raise ValueError('corpus mismatch')
        with zipfile.ZipFile(archive) as z,z.open(domain+'.jsonl') as f:
            for line in f:
                r=json.loads(line);pid=f"mtrag:{domain}:{r['_id']}"
                if pid in wanted:
                    if pid in texts:raise ValueError('duplicate source')
                    texts[pid]=r['text']
    if texts.keys()!=wanted:raise ValueError('candidate source missing')
    identity={'model_files':{p.name:digest(p) for p in a.model.iterdir() if p.is_file() and p.suffix in ('.json','.safetensors','.model','.bin')},
              'hybrid_sha256':digest(a.hybrid/'cases.json.gz'),'source_manifest_sha256':digest(a.manifest),
              'input_sha256':hashlib.sha256(json.dumps([(i,p,cases[i]['query'],texts[p]) for i,p in needed],ensure_ascii=False).encode()).hexdigest(),
              'recipe':'FP16 batch4 query + full passage text; no truncation; stable ID ties; unmodified pretrained BGE reranker'}
    a.output.mkdir(parents=True,exist_ok=False)
    print(f'unique pairs {len(needed)}, arm pairs {sum(len(r["variants"][arm]["ranking"]) for r in cases for arm in arms_to_score)}',flush=True)
    start=time.monotonic()
    if a.replay:
        old=json.loads((a.replay/'identity.json').read_text())
        if old!=identity:raise ValueError('replay identity mismatch')
        with gzip.open(a.replay/'scores.json.gz','rt') as f:scored=json.load(f)
    else:
        import torch
        from transformers import AutoTokenizer,AutoModelForSequenceClassification
        tok=AutoTokenizer.from_pretrained(a.model,local_files_only=True)
        model=AutoModelForSequenceClassification.from_pretrained(a.model,local_files_only=True,torch_dtype=torch.float16).to('cuda').eval()
        scored=[]
        for offset in range(0,len(needed),4):
            batch=needed[offset:offset+4]
            encoded=tok([cases[i]['query'] for i,p in batch],[texts[p] for i,p in batch],padding=True,truncation=False,return_tensors='pt')
            length=encoded['input_ids'].shape[1]
            if length>8192:raise ValueError('CE input exceeds8192')
            with torch.inference_mode():values=model(**{k:v.to('cuda') for k,v in encoded.items()}).logits.view(-1).float().cpu().tolist()
            for (i,p),score in zip(batch,values):scored.append({'case_index':i,'passage_id':p,'score':score,'padded_input_tokens':length})
    lookup={(r['case_index'],r['passage_id']):r['score'] for r in scored}
    if len(lookup)!=len(scored) or set(lookup)!=set(needed) or not all(math.isfinite(x) for x in lookup.values()):raise ValueError('invalid pair scores')
    results=[]
    for i,r in enumerate(cases):
        arms={}
        for arm in arms_to_score:
            ids=r['variants'][arm]['ranking'];ordered=rerank(ids,{p:lookup[i,p] for p in ids})
            arms[arm]={'before':at5(ids,set(r['gold'])),'after':at5(ordered,set(r['gold'])),'ranking':ordered,
                       'candidate_recall@20':r['variants'][arm]['metrics']['recall@20']}
        results.append({'case_id':r['case_id'],'group_id':r['group_id'],'domain':r['domain'],'gold':r['gold'],'arms':arms})
    summary={arm:{stage:{m:sum(r['arms'][arm][stage][m] for r in results)/len(results) for m in results[0]['arms'][arm][stage]} for stage in ('before','after')} for arm in arms_to_score}
    paired={arm:{m:{'better':sum(r['arms'][arm]['after'][m]>r['arms'][arms_to_score[0]]['after'][m]+1e-12 for r in results),'worse':sum(r['arms'][arm]['after'][m]<r['arms'][arms_to_score[0]]['after'][m]-1e-12 for r in results)} for m in results[0]['arms'][arm]['after']} for arm in arms_to_score[1:]}
    report={'scope':'fixed supplied cases, local CE top5 only; no pack/tool/answer outcome','api_calls':0,'unique_pairs':len(needed),'new_model_pairs':0 if a.replay else len(needed),'scoring_and_ranking_seconds':time.monotonic()-start,'max_padded_input_tokens':max(r['padded_input_tokens'] for r in scored),'summary':summary,'paired_after_vs_current':paired}
    for name,value in [('scores',scored),('cases',results)]:
        with gzip.open(a.output/(name+'.json.gz'),'wt') as f:json.dump(value,f,ensure_ascii=False)
    (a.output/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('hybrid','manifest','corpora','model','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--replay',type=Path)
    p.add_argument('--arms',nargs='+',metavar='ARM')
    run(p.parse_args())
