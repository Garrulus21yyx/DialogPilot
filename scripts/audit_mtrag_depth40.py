"""Independent replay of provenance, score ordering, packing and grouped interval."""
import hashlib,json
from collections import defaultdict
import numpy as np
from scripts.eval_mtrag_depth40 import OUT,BASE,PREV
from scripts.audit_rag_fresh100 import data
from scripts.replay_rag_rank_selection import read,digest,pack
from scripts.run_rag_fresh100 import ROOT
from mcp.rank_fusion import fuse_rankings

def main():
 rows=read(OUT/'cases.json.gz');old={r['id']:r for r in read(BASE/'cases.json.gz')};sel=read(ROOT/'selection.json');manifest=read(OUT/'manifest.json')
 assert len(rows)==100 and len({r['id'] for r in rows})==100
 for p,h in manifest['source_hashes'].items():assert digest(p)==h
 src,txt,metric,_=data('MTRAG',[{**r,'pool':list(set(sum(r['routes'].values(),[])))} for r in rows],sel)
 scores={}
 for p in [BASE/'scores.json.gz',PREV/'added-scores.json.gz',OUT/'added-scores.json.gz']:
  for s in read(p):
   if s['cid'] in txt:
    assert s['query']==old[s['id']]['query'] and s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest() and np.isfinite(s['score'])
    scores[s['id'],s['cid']]=s['score']
 groups=defaultdict(list)
 for r in rows:
  assert r['query']==old[r['id']]['query'] and {k:v[:20] for k,v in r['routes'].items()}==old[r['id']]['routes']
  pool=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));assert pool==r['pool']
  order=sorted(pool,key=lambda d:(-scores[r['id'],d],d));assert order==r['ce_order']
  for a,o in [('candidate',order),('baseline',old[r['id']]['ce_order'])]:
   ids,tokens=pack(r['query'],o,src);assert {**metric(r,ids),'ids':ids,'tokens':tokens}==r[a];assert len(ids)<=5 and tokens<=2600
  groups[r['group']].append(r['candidate']['recall']-r['baseline']['recall'])
 sums=np.array([sum(v) for v in groups.values()]);counts=np.array([len(v) for v in groups.values()]);rng=np.random.default_rng(20260908);idx=rng.integers(0,len(groups),(20000,len(groups)));d=sums[idx].sum(1)/counts[idx].sum(1)
 report={'n':100,'groups':len(groups),'source_query_route_prefix_score_and_wire_verified':True,'recall_delta':sums.sum()/100,'cluster_bootstrap_95':np.quantile(d,[.025,.975]).tolist()}
 (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
if __name__=='__main__':main()
