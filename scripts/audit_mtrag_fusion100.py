"""Replay with independently calculated RRF/quota and actual packing."""
import hashlib,json
import numpy as np
from scripts.eval_mtrag_fusion100 import OUT,DEPTH,BASE,PREV
from scripts.audit_rag_fresh100 import data
from scripts.replay_rag_rank_selection import read,digest,pack
from scripts.run_rag_fresh100 import ROOT

def rank(routes,alpha):
 scores={};best={}
 for k,w in [('dense',alpha),('bm25',1-alpha)]:
  for i,c in enumerate(routes[k],1):scores[c]=scores.get(c,0)+w/(10+i);best[c]=min(i,best.get(c,i))
 return sorted(scores,key=lambda c:(-scores[c],best[c],c))[:20]
def main():
 rows=read(OUT/'cases.json.gz');manifest=read(OUT/'manifest.json');sel=read(ROOT/'selection.json');olds={r['id']:r for r in read(BASE/'cases.json.gz')}
 assert len(rows)==len({r['id'] for r in rows})==100
 for p,h in manifest['source_hashes'].items():assert digest(p)==h
 src,txt,metric,_=data('MTRAG',[{**r,'pool':list(set(sum(r['routes'].values(),[])))} for r in rows],sel);ss={}
 for p in [BASE/'scores.json.gz',PREV/'added-scores.json.gz',DEPTH/'added-scores.json.gz',OUT/'added-scores.json.gz']:
  for s in read(p):
   if s['cid'] in txt:
    assert s['query']==olds[s['id']]['query'] and s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest() and np.isfinite(s['score']);ss[s['id'],s['cid']]=s['score']
 for r in rows:
  assert r['query']==olds[r['id']]['query']
  eq=rank(r['routes'],.5);quota=[]
  for c in r['routes']['dense'][:10]+r['routes']['bm25'][:10]+eq:
   if c not in quota:quota.append(c)
  expected={'equal':eq,'dense75':rank(r['routes'],.75),'quota10':quota[:20]}
  for a,pool in expected.items():
   arm=r['arms'][a];assert arm['pool']==pool and len(set(pool))==len(pool)==20
   if a=='quota10':assert set(r['routes']['dense'][:10]+r['routes']['bm25'][:10])<=set(pool)
   order=sorted(pool,key=lambda c:(-ss[r['id'],c],c));assert arm['ce_order']==order
   ids,t=pack(r['query'],order,src);assert {**metric(r,ids),'ids':ids,'tokens':t}==arm['wire'];assert len(ids)<=5 and t<=2600
   gold={g['id'] for g in r['gold_trace']};assert arm['candidate_recall']==len(gold&set(pool))/len(gold)
 report={'n':100,'arms':3,'independent_rrf_and_quota_verified':True,'scores_source_query_pack_wire_verified':True,'new_ce_pairs':len(read(OUT/'added-scores.json.gz')),'api_calls':0}
 (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
if __name__=='__main__':main()
