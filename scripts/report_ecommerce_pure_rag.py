"""Offline source-level metrics for the pure RAG replay, with all cases retained."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import numpy as np


def metrics(sources, gold):
    sources = list(dict.fromkeys(sources))
    gold = set(gold)
    ranks = [i for i, source in enumerate(sources, 1) if source in gold]
    return {'recall5':sum(i <= 5 for i in ranks)/len(gold),
            'recall20':sum(i <= 20 for i in ranks)/len(gold),
            'mrr5':1/min(ranks) if ranks and min(ranks)<=5 else 0.0,
            'ndcg5':sum(1/math.log2(i+1) for i in ranks if i<=5)/sum(1/math.log2(i+1) for i in range(1,min(5,len(gold))+1))}


def report(root):
    labels={c['id']:c for c in json.loads((root/'labels.json').read_text())}
    docs={d['source_id']:d['content'] for d in json.loads(Path('data/eval/ecommerce-rag-v1/corpus.json').read_text())}
    rows=[json.loads(l) for l in gzip.decompress((root/'runtime/pure-cases.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows)==2*len(labels) and len({(r['arm'],r['id']) for r in rows})==len(rows)
    scored=[];audited=0
    for r in rows:
        label=labels[r['id']]; gold=label['knowledge_sources']
        fused=r['retrieval'][-1]['fused_candidates'] if r['retrieval'] else []
        byid={c['chunk_id']:c for c in fused}
        ordered=r['rerank'][-1]['ordered_ids'] if r['rerank'] else []
        if fused: assert set(ordered)==set(byid), (r['id'],'missing rerank capture')
        wire=r['wire'].get('evidence',[])
        for e in wire:
            source=e['source'];body=docs[source['source_id']]
            assert e['text']==body[source['start_char']:source['end_char']]
            assert hashlib.sha256(body.encode()).hexdigest()==source['checksum'];audited+=1
        scored.append({'id':r['id'],'arm':r['arm'],'split':label['evaluation_split'],
            'category':label['category'],'group':label['group_id'],
            'query':r['query'],'rewrite_error':r['rewrite_error'],
            'candidate':metrics([c['source_id'] for c in fused],gold),
            'rerank':metrics([byid[c]['source_id'] for c in ordered],gold),
            'wire':metrics([e['source']['source_id'] for e in wire],gold),
            'status':r['result']['status'],'ms':r['measured_ms']})
    summaries={}
    for arm in ['history_concat','standalone_raw']:
        summaries[arm]={}
        for split in ['all','consumed_dev','consumed_heldout']:
            subset=[r for r in scored if r['arm']==arm and (split=='all' or r['split']==split)]
            summaries[arm][split]={'n':len(subset),**{stage:{k:sum(r[stage][k] for r in subset)/len(subset) for k in ['recall5','recall20','mrr5','ndcg5']} for stage in ['candidate','rerank','wire']},'p50_ms':float(np.median([r['ms'] for r in subset])),'p95_ms':float(np.quantile([r['ms'] for r in subset],.95))}
    paired=[]
    for cid in labels:
        a,b=([r for r in scored if r['id']==cid and r['arm']==arm][0] for arm in ['history_concat','standalone_raw'])
        paired.append({'id':cid,'wire_delta':b['wire']['recall5']-a['wire']['recall5'],'rerank_mrr_delta':b['rerank']['mrr5']-a['rerank']['mrr5']})
    calls=[c for r in rows for c in r['rewrite_calls']]
    usage={'calls':len(calls),'output_tokens':0,'input_tokens':0,'cache_read_input_tokens':0}
    for c in calls:
        u=c.get('response',{}).get('usage',{})
        for k in ['output_tokens','input_tokens','cache_read_input_tokens']:usage[k]+=u.get(k,0)
    result={'scope':'100 consumed synthetic knowledge cases / 50 groups; source-level, no final answers or business execution','summaries':summaries,'usage':usage,'source_audit_occurrences':audited,'rewrite_failures':sum(bool(r['rewrite_error']) for r in scored if r['arm']=='standalone_raw'),'paired':{'wire_rescue':sum(p['wire_delta']>0 for p in paired),'wire_hurt':sum(p['wire_delta']<0 for p in paired)},'limitations':['Short authored documents, not minimum necessary spans or long-document chunking evaluation','Historical inputs supplied directly to QueryTransformer; no Conversation Agent, memory or tool selection evaluated','Latency includes projection/capture and cold start, excludes query rewriting; fixed arm order is not a performance comparison','Consumed splits, not fresh heldout; no production strategy adoption based on this run']}
    for name,value in [('report.json',result),('scored-cases.json',scored),('paired-cases.json',paired)]:
        (root/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();print(json.dumps(report(a.root),ensure_ascii=False,indent=2))
