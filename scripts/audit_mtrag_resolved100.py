"""Zero-model reproduction of scores, gold loss accounting, packing and paired intervals."""
import hashlib,json,math
from collections import defaultdict
import numpy as np
from scripts.diagnose_mtrag_resolved100 import OUT,BASE
from scripts.replay_rag_rank_selection import read,pack
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
from mcp.rank_fusion import fuse_rankings

def main():
 manifest=read(OUT/'manifest.json')
 for p,h in manifest['source_sha256'].items():assert hashlib.sha256(__import__('pathlib').Path(p).read_bytes()).hexdigest()==h
 old={r['id']:r for r in read(BASE/'cases.json.gz')};rows=read(OUT/'cases.json.gz');selection=read(ROOT/'selection.json');cases={c['id']:c for c in selection['MTRAG']}
 combined=[{**r,'pool':list(set(r['routes']['dense'])|set(r['routes']['bm25']))} for r in old.values()];src,txt,metric,_=data('MTRAG',combined,selection);scores={}
 for s in read(BASE/'scores.json.gz')+read(OUT/'added-scores.json.gz'):
  assert s['query']==old[s['id']]['query'] and s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest() and math.isfinite(s['score'])
  assert (s['id'],s['cid']) not in scores;scores[s['id'],s['cid']]=s['score']
 assert len(rows)==100 and {r['id'] for r in rows}==set(old)
 for r in rows:
  o=old[r['id']];assert r['query']==o['query'];ce=sorted(o['pool'],key=lambda c:(-scores[r['id'],c],c));assert ce==o['ce_order']
  orders={'baseline':ce,'dense20':sorted(o['routes']['dense'],key=lambda c:(-scores[r['id'],c],c)),'ce_recall_mix':list(fuse_rankings({'ce':ce,'recall':o['pool']},weights={'ce':.75,'recall':.25},rrf_k=10,top_k=20))}
  for arm,order in orders.items():
   ii,tt=pack(r['query'],order,src);assert r['arms'][arm]=={**metric(r,ii),'ids':ii,'tokens':tt}
  assert r['arms']['baseline']==o['model']
  gold={e['document_id'] for e in cases[r['id']]['evidence']};assert {g['gold'] for g in r['gold_trace']}==gold
  stages={'dense20':o['routes']['dense'],'bm25_20':o['routes']['bm25'],'union40':list(dict.fromkeys(o['routes']['dense']+o['routes']['bm25'])),'fusion20':o['pool'],'ce5':ce[:5],'wire5':o['model']['ids']}
  assert r['stages']=={k:len(set(v)&gold)/len(gold) for k,v in stages.items()}
  for g in r['gold_trace']:
   assert g['ranks']=={k:v.index(g['gold'])+1 if g['gold'] in v else None for k,v in stages.items()}
   assert g['first_loss']==next((k for k in ['union40','fusion20','ce5','wire5'] if g['ranks'][k] is None),'retained')
 intervals={}
 for arm in ['dense20','ce_recall_mix']:
  groups=defaultdict(list)
  for r in rows:groups[r['group']].append(r['arms'][arm]['recall']-r['arms']['baseline']['recall'])
  sums=np.array([sum(v) for v in groups.values()]);counts=np.array([len(v) for v in groups.values()]);sample=np.random.default_rng(20260908).integers(0,len(groups),size=(20000,len(groups)));delta=sums[sample].sum(axis=1)/counts[sample].sum(axis=1)
  intervals[arm]=np.quantile(delta,[.025,.975]).tolist()
 report={'n':100,'groups':len(groups),'score_pairs':len(scores),'all_stage_and_wire_replays_passed':True,'paired_cluster_recall_delta_95':intervals}
 if (OUT/'flash6.jsonl').exists():
  ff=read(OUT/'flash6.jsonl',True)
  if len(ff)==6:
   failures=[r for r in ff if 'flash' not in r]
   ff=[r for r in ff if 'flash' in r]
   assert len(failures)==1 and len(ff)==5
   for r in ff:
    assert r['query']==old[r['id']]['query'] and r['pool']==old[r['id']]['pool'];assert set(r['rank']['ordered_ids'])==set(r['pool'])
    ii,tt=pack(r['query'],r['rank']['ordered_ids'],src);assert r['flash']=={**metric(r,ii),'ids':ii,'tokens':tt}
   report['flash6']={'planned':6,'paired':len(ff),'capture_failures':len(failures),'lost_call_count_bound':[1,2],'recorded_calls':sum(len(r['calls']) for r in ff),'fallbacks':sum(r['rank']['error'] is not None for r in ff),'summary':{arm:{k:sum(r[arm][k] for r in ff)/len(ff) for k in ['recall','mrr','ndcg']} for arm in ['baseline','flash']},'better':sum(r['flash']['recall']>r['baseline']['recall'] for r in ff),'worse':sum(r['flash']['recall']<r['baseline']['recall'] for r in ff)}
 (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
