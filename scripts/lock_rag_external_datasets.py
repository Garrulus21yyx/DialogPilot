"""Pin MTRAG conversation-group split and full WixQA external test identities."""
import argparse,concurrent.futures,hashlib,json,math,urllib.request
from pathlib import Path

MTRAG_REV='2c618bb98db3c8526433e22d8a2f7320f10a7470'
WIX_REV='d662dc42479c14e202eccd832f8c4b66a035c4cc'
DOMAINS=('clapnq','cloud','fiqa','govt')
def sha(b):return hashlib.sha256(b).hexdigest()
def split_conversations(tasks):
    grouped={d:{} for d in DOMAINS}
    for task in tasks:
        domain=next(d for d in DOMAINS if f'-{"ibmcloud" if d == "cloud" else d}-' in task['Collection'])
        cid=task['conversation_id']
        grouped[domain].setdefault(cid,[]).append(task['task_id'])
    rows=[]
    for domain,mapping in grouped.items():
        cs=[{'conversation_id':cid,'task_ids':sorted(ids)} for cid,ids in mapping.items()]
        assert len(cs)>1
        cs.sort(key=lambda c:sha(('mtrag-dialogpilot-v1\0'+c['conversation_id']).encode()))
        for i,c in enumerate(cs):rows.append({**c,'domain':domain,'split':'dev' if i<math.floor(.7*len(cs)) else 'heldout'})
    assert len({r['conversation_id'] for r in rows})==len(rows)
    ids=[i for r in rows for i in r['task_ids']];assert len(set(ids))==len(ids)
    return rows

def run(args):
    args.output.mkdir(parents=True,exist_ok=False);args.cache.mkdir(parents=True,exist_ok=True)
    paths={'conversations.json':f'https://raw.githubusercontent.com/IBM/mt-rag-benchmark/{MTRAG_REV}/mtrag-human/conversations/conversations.json'}
    paths['reference.jsonl']=f'https://raw.githubusercontent.com/IBM/mt-rag-benchmark/{MTRAG_REV}/mtrag-human/generation_tasks/reference.jsonl'
    for d in DOMAINS:
        base=f'https://raw.githubusercontent.com/IBM/mt-rag-benchmark/{MTRAG_REV}/mtrag-human/retrieval_tasks/{d}'
        for mode in ('lastturn','questions','rewrite'):paths[f'{d}-{mode}.jsonl']=f'{base}/{d}_{mode}.jsonl'
        paths[f'{d}-qrels.tsv']=f'{base}/qrels/dev.tsv'
    for name,remote in [('wix-corpus','wix_kb_corpus/wix_kb_corpus.jsonl'),('wix-expertwritten','wixqa_expertwritten/test.jsonl'),('wix-simulated','wixqa_simulated/test.jsonl')]:
        paths[name+'.jsonl']=f'https://huggingface.co/datasets/Wix/WixQA/resolve/{WIX_REV}/{remote}'
    def fetch(item):
        name,url=item;p=args.cache/name
        if not p.exists():
            with urllib.request.urlopen(url,timeout=120) as response:data=response.read()
            p.write_bytes(data)
        data=p.read_bytes();return name,{'url':url,'sha256':sha(data),'bytes':len(data)}
    sources=dict(concurrent.futures.ThreadPoolExecutor(max_workers=4).map(fetch,paths.items()))
    tasks=[json.loads(l) for l in (args.cache/'reference.jsonl').read_text().splitlines()];rows=split_conversations(tasks)
    taskmap={t:r for r in rows for t in r['task_ids']}
    stats={}
    for d in DOMAINS:
        queries=[json.loads(l) for l in (args.cache/f'{d}-lastturn.jsonl').read_text().splitlines()]
        ids={str(q['_id']) for q in queries};assert ids<=taskmap.keys()
        for t in ids:assert taskmap[t]['domain']==d
        qrelids={l.split('\t')[0] for l in (args.cache/f'{d}-qrels.tsv').read_text().splitlines()[1:]};assert qrelids<=ids
        stats[d]={s:{'conversations':sum(r['domain']==d and r['split']==s for r in rows),'retrieval_queries':sum(taskmap[t]['split']==s for t in ids),'with_qrels':sum(taskmap[t]['split']==s for t in qrelids)} for s in ('dev','heldout')}
    manifest={'dataset':'MTRAG human','revision':MTRAG_REV,'split_algorithm':'mtrag-dialogpilot-conversation-hash-v1','identity_rule':'official generation_tasks/reference.jsonl conversation_id and task_id; validated against retrieval query IDs','split_is_official':False,'stats':stats,'groups':rows,'source_files':{k:v for k,v in sources.items() if not k.startswith('wix')}}
    (args.output/'mtrag-split.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    wix={}
    for name in ('wix-corpus','wix-expertwritten','wix-simulated'):
        data=[json.loads(l) for l in (args.cache/(name+'.jsonl')).read_text().splitlines()]
        wix[name]={'rows':len(data),'row_identity':'source revision + zero-based row index','row_sha256':[sha(json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()) for r in data]}
    (args.output/'wixqa-test-lock.json').write_text(json.dumps({'dataset':'WixQA','revision':WIX_REV,'scope':'full ExpertWritten+Simulated test; article relevance only; evaluation NOT RUN','configs':wix,'source_files':{k:v for k,v in sources.items() if k.startswith('wix')}},indent=2)+'\n')
    print(json.dumps({'mtrag':stats,'wix':{k:v['rows'] for k,v in wix.items()}},indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True);run(p.parse_args())
