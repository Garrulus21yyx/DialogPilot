"""Zero-inference source, rank, and wire audit for dataset-specific diagnostics."""
import hashlib,json,math
from pathlib import Path
from scripts.replay_rag_rank_selection import read,pack
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
from mcp.rank_fusion import fuse_rankings
OUT=Path('artifacts/eval/rag-dataset-specific-2026-09-08')
def checked_scores(items,texts):
    scores={}
    for s in items:
        assert s['text_sha256']==hashlib.sha256(texts[s['cid']].encode()).hexdigest() and math.isfinite(s['score'])
        key=(s.get('case_id',s.get('id')),s['cid']);assert key not in scores;scores[key]=s
    return scores

def verify(row,pool,scores,src,metric,tie,query=None):
    q=query or row['query']
    assert all(scores[row['id'],cid]['query']==q for cid in pool)
    ce=sorted(pool,key=lambda c:(-scores[row['id'],c]['score'],tie(c)));ids,tokens=pack(q,ce,src)
    return {**metric(row,ids),'ids':ids,'tokens':tokens}

def main():
    selection=read(ROOT/'selection.json');report={}
    pure=Path('artifacts/eval/rag-pure-query20-2026-09-08');caps=read(pure/'captures.jsonl',True);inputs=read('artifacts/eval/rag-agent-context20-2026-09-08/captures.jsonl',True)
    for c,i in zip(caps,inputs,strict=True):
        assert c['id']==i['id'] and len(c['calls'])==1
        ms=c['calls'][0]['request']['messages'];assert [(m['role'],m['content']) for m in ms[:-1]]==list(zip(i['roles'],i['original_history']))
        assert ms[-1]['content']=='CURRENT USER MESSAGE:\n'+i['raw_query']
    src,txt,metric,_=data('Doc2Dial',read(ROOT/'Doc2Dial/cases.json.gz'),selection);scores=checked_scores(read(pure/'scores.json.gz'),txt)
    for r in read(pure/'retrieval.json'):
        pool=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20))
        assert verify(r,pool,scores,src,metric,lambda c:c,r['queries'][0])==r['agent']
    report['Doc2Dial']={'n':20,'scores':len(scores),'provider_history_and_wire_verified':True}
    rr=read(OUT/'MTRAG.json.gz');old=read(ROOT/'MTRAG/cases.json.gz');expanded=[{**r,'pool':d['pools']['union40']} for r,d in zip(old,rr,strict=True)]
    src,txt,metric,_=data('MTRAG',expanded,selection);scores=checked_scores(read(ROOT/'MTRAG/scores.json.gz')+read(OUT/'MTRAG-added-scores.json.gz'),txt)
    for row,r in zip(rr,read(OUT/'MTRAG-final.json.gz'),strict=True):
        for a,result in r['arms'].items():assert verify(row,row['pools'][a],scores,src,metric,lambda c:c)==result
    report['MTRAG']={'n':100,'scores':len(scores),'all_arms_wire_verified':True}
    old=read(ROOT/'WixQA/cases.json.gz');src,txt,metric,_=data('WixQA',old,selection);scores=checked_scores(read(ROOT/'WixQA/scores.json.gz'),txt)
    for row,r in zip(old,read(OUT/'WixQA.json.gz'),strict=True):
        union=list(fuse_rankings(row['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40))
        ar={k:list(dict.fromkeys(src[cid].document_id for cid in v)) for k,v in row['routes'].items()};aa=list(fuse_rankings(ar,weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));reps=[next(c for c in union if src[c].document_id==a) for a in aa];expected=list(dict.fromkeys(reps+union))[:20]
        assert r['arms']['article_rrf20']['pool']==expected
        for a,result in r['arms'].items():assert verify(row,result['pool'],scores,src,metric,lambda c:union.index(c))=={k:v for k,v in result.items() if k!='pool'}
    report['WixQA']={'n':100,'scores':len(scores),'all_arms_wire_verified':True}
    (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
