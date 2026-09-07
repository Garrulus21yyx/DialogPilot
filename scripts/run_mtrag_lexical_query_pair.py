"""Full-domain BM25 with query-bound sufficient statistics; no model inference."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import zipfile
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from application.chinese_lexical import postgres_lexical_document, TOKENIZER_VERSION
from scripts.adapt_mtrag_retrieval_dataset import digest, DOMAINS, rows

SEED = 'mtrag-g4-lexical-dev32-v1'
MODES = ('lastturn','questions','rewrite')


def select(cases, per_domain):
    selected=[]
    for domain in DOMAINS:
        groups=defaultdict(list)
        for c in cases:
            if c['split']=='dev' and domain in c['query_types']:
                groups[c['group_id']].append(c)
        if len(groups)<per_domain:
            raise ValueError('insufficient development conversations')
        key=lambda x:hashlib.sha256((SEED+'\0'+x).encode()).hexdigest()
        for group in sorted(groups,key=key)[:per_domain]:
            selected.append(min(groups[group],key=lambda c:key(c['id'])))
    return selected


def stream_bm25(queries, documents):
    terms=[tuple(dict.fromkeys(postgres_lexical_document(q).split())) for q in queries]
    wanted=set(t for ts in terms for t in ts)
    postings=defaultdict(list)
    ids,lengths=[],[]
    for doc_id,text in documents:
        tokens=postgres_lexical_document(text).split()
        index=len(ids);ids.append(doc_id);lengths.append(len(tokens))
        frequencies=Counter(t for t in tokens if t in wanted)
        for term,frequency in frequencies.items():
            postings[term].append((index,frequency))
    if not ids or len(set(ids))!=len(ids):
        raise ValueError('empty corpus or duplicate IDs')
    lens=np.asarray(lengths,dtype=np.float64)
    average=max(float(lens.mean()),1.0)
    scores=np.zeros((len(queries),len(ids)),dtype=np.float64)
    for qi,ts in enumerate(terms):
        for term in ts:
            ps=postings.get(term,[])
            if not ps:continue
            index=np.asarray([p[0] for p in ps],dtype=np.int64)
            frequency=np.asarray([p[1] for p in ps],dtype=np.float64)
            df=len(ps)
            idf=math.log(1+(len(ids)-df+.5)/(df+.5))
            scores[qi,index]+=idf*frequency*2.2/(frequency+1.2*(.25+.75*lens[index]/average))
    return ids,scores


def metrics(ranked,gold):
    if not gold:raise ValueError('positive qrels required')
    if len(ranked)!=len(set(ranked)):raise ValueError('duplicate ranked IDs')
    values={f'recall@{k}':len(set(ranked[:k]) & gold)/len(gold) for k in (1,5,20)}
    values['mrr@20']=next((1/(i+1) for i,d in enumerate(ranked[:20]) if d in gold),0.)
    dcg=sum(1/math.log2(i+2) for i,d in enumerate(ranked[:20]) if d in gold)
    ideal=sum(1/math.log2(i+2) for i in range(min(20,len(gold))))
    values['ndcg@20']=dcg/ideal
    return values


def run(args):
    manifest=json.loads((args.dataset/'manifest.json').read_text())
    for file,key in [('cases.jsonl','cases_sha256'),('corpus.jsonl','corpus_sha256')]:
        if digest(args.dataset/file)!=manifest[key]:raise ValueError('adapted dataset checksum mismatch')
    if digest(args.dataset/'official-queries.jsonl')!=manifest['source']['official_queries_sha256']:
        raise ValueError('query variants checksum mismatch')
    selected=select(rows(args.dataset/'cases.jsonl'),args.per_domain)
    variants={r['task_id']:r['queries'] for r in rows(args.dataset/'official-queries.jsonl')}
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'selection.json').write_text(json.dumps({'seed':SEED,'cases':selected},ensure_ascii=False,indent=2)+'\n')
    results=[]; timings={}
    for domain in DOMAINS:
        cases=[c for c in selected if domain in c['query_types']]
        pairs=[(c,mode) for c in cases for mode in MODES]
        queries=[variants[c['id']][mode] for c,mode in pairs]
        archive=args.corpora/f'{domain}.jsonl.zip'
        if digest(archive)!=manifest['source']['archives'][domain]['sha256']:
            raise ValueError('corpus archive differs from adapted dataset')
        def documents():
            with zipfile.ZipFile(archive) as z,z.open(domain+'.jsonl') as f:
                for line in f:
                    r=json.loads(line)
                    if r['text'].strip():yield f"mtrag:{domain}:{r['_id']}",r['text']
        start=time.monotonic();ids,scores=stream_bm25(queries,documents())
        timings[domain]={'passages':len(ids),'statistics_and_scoring_seconds':time.monotonic()-start}
        for i,(case,mode) in enumerate(pairs):
            positive=np.flatnonzero(scores[i]>0)
            order=sorted(positive,key=lambda j:(-scores[i,j],ids[j]))[:20]
            ranked=[ids[j] for j in order]
            gold={e['document_id'] for e in case['evidence'] if e['relevance']>0}
            results.append({'case_id':case['id'],'group_id':case['group_id'],'domain':domain,'mode':mode,
                'query':queries[i],'ranking':[{'id':ids[j],'score':float(scores[i,j])} for j in order],
                'gold':sorted(gold),'metrics':metrics(ranked,gold)})
    summary={}
    for scope in (*DOMAINS,'all'):
        summary[scope]={}
        for mode in MODES:
            rs=[r for r in results if r['mode']==mode and (scope=='all' or r['domain']==scope)]
            summary[scope][mode]={k:sum(r['metrics'][k] for r in rs)/len(rs) for k in rs[0]['metrics']}
    paired={}
    for mode in MODES[1:]:
        pairs=[(r,next(x for x in results if x['case_id']==r['case_id'] and x['mode']==mode)) for r in results if r['mode']=='lastturn']
        paired[mode]={k:{'better':sum(b['metrics'][k]>a['metrics'][k]+1e-12 for a,b in pairs),
                         'worse':sum(b['metrics'][k]<a['metrics'][k]-1e-12 for a,b in pairs)} for k in pairs[0][0]['metrics']}
    with gzip.open(args.output/'results.json.gz','wt') as f:json.dump(results,f,ensure_ascii=False)
    report={'scope':'Offline official passage/body BM25; domain-separated; development only; not PostgreSQL or Agent/answer evaluation',
            'api_calls':0,'embedding_calls':0,'cases':len(selected),'seed':SEED,'tokenizer':TOKENIZER_VERSION,'bm25':{'k1':1.2,'b':.75},
            'dataset_manifest_sha256':digest(args.dataset/'manifest.json'),'summary':summary,'paired_vs_lastturn':paired,'timings':timings}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'summary':summary['all'],'paired':paired,'timings':timings},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','corpora','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--per-domain',type=int,default=8)
    a=p.parse_args()
    if a.per_domain<1:raise ValueError('positive sample count required')
    run(a)
