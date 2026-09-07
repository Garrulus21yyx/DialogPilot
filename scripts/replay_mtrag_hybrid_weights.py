"""Same-query/same-budget fusion replay; reject incomplete or mismatched runs."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mcp.rank_fusion import fuse_rankings
from scripts.run_mtrag_lexical_query_pair import metrics

WEIGHTS=(0.,.25,.5,.75,1.)


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def paired_rows(dense,lexical):
    bm={r['case_id']:r for r in lexical if r['mode']=='rewrite'}
    if len(bm)!=sum(r['mode']=='rewrite' for r in lexical):raise ValueError('duplicate lexical cases')
    if len({r['case_id'] for r in dense})!=len(dense) or {r['case_id'] for r in dense}!=set(bm):
        raise ValueError('case coverage mismatch')
    result=[]
    for d in dense:
        b=bm[d['case_id']]
        if any(d[k]!=b[k] for k in ('query','group_id','domain','gold')):
            raise ValueError('query/group/domain/gold mismatch')
        for row in (d,b):
            ids=[x['id'] for x in row['ranking']]
            if len(ids)!=len(set(ids)) or any(not x.startswith('mtrag:'+d['domain']+':') for x in ids):
                raise ValueError('invalid ranked source identity')
        ranks={'dense':[r['id'] for r in d['ranking'][:20]],'lexical':[r['id'] for r in b['ranking'][:20]]}
        gold=set(d['gold']);union=set(ranks['dense'])|set(ranks['lexical'])
        variants={}
        for alpha in WEIGHTS:
            ranking=fuse_rankings(ranks,weights={'dense':alpha,'lexical':1-alpha},rrf_k=10,top_k=20)
            variants[str(alpha)]={'ranking':ranking,'metrics':metrics(ranking,gold),
                'gold_removed_from_union':sorted((gold & union)-set(ranking))}
        result.append({'case_id':d['case_id'],'domain':d['domain'],'group_id':d['group_id'],'query':d['query'],
            'gold':d['gold'],'variants':variants,'union_diagnostic':{'budget':len(union),
                'recall':len(gold & union)/len(gold),'dense_only_gold':sorted((gold & set(ranks['dense']))-set(ranks['lexical'])),
                'lexical_only_gold':sorted((gold & set(ranks['lexical']))-set(ranks['dense']))}})
    return result


def report(rows):
    summary={};paired={}
    for scope in (*sorted({r['domain'] for r in rows}),'all'):
        rs=[r for r in rows if scope=='all' or r['domain']==scope]
        summary[scope]={}
        for a in WEIGHTS:
            key=str(a);ms=[r['variants'][key]['metrics'] for r in rs]
            summary[scope][key]={k:sum(m[k] for m in ms)/len(ms) for k in ms[0]}
        summary[scope]['union_diagnostic_recall']=sum(r['union_diagnostic']['recall'] for r in rs)/len(rs)
    for a in WEIGHTS[1:]:
        paired[str(a)]={}
        for metric in rows[0]['variants']['0.0']['metrics']:
            deltas=[r['variants'][str(a)]['metrics'][metric]-r['variants']['0.0']['metrics'][metric] for r in rows]
            paired[str(a)][metric]={'better':sum(d>1e-12 for d in deltas),'worse':sum(d< -1e-12 for d in deltas)}
    return {'cases':len(rows),'weights':list(WEIGHTS),'candidate_k':20,'source_k':20,'rrf_k':10,
        'api_calls':0,'summary':summary,'paired_vs_bm25':paired,
        'scope':'Development replay, same fixed official query; no reranking/tool/answer outcome; union is larger-budget diagnostic only'}


def main(a):
    progress=json.loads((a.dense/'progress.json').read_text())
    if progress['status']!='COMPLETE':raise ValueError('dense run incomplete')
    identity=json.loads((a.dense/'identity.json').read_text())
    if identity['query_results_sha256']!=sha(a.lexical/'results.json.gz'):
        raise ValueError('lexical input identity differs from dense run')
    with gzip.open(a.dense/'results.json.gz','rt') as f:ds=json.load(f)
    with gzip.open(a.lexical/'results.json.gz','rt') as f:bs=json.load(f)
    rows=paired_rows(ds,bs);summary=report(rows)
    summary['input_hashes']={'dense':sha(a.dense/'results.json.gz'),'lexical':sha(a.lexical/'results.json.gz'),'identity':sha(a.dense/'identity.json')}
    a.output.mkdir(parents=True,exist_ok=False)
    with gzip.open(a.output/'cases.json.gz','wt') as f:json.dump(rows,f,ensure_ascii=False)
    (a.output/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary['summary']['all'],indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dense','lexical','output'):p.add_argument('--'+name,type=Path,required=True)
    main(p.parse_args())
