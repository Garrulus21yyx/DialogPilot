"""Exact full-domain gold ranks at fixed query; no index or policy change."""
import json
import numpy as np
from scripts.diagnose_mtrag_resolved100 import OUT,BASE
from scripts.replay_rag_rank_selection import read
import scripts.run_rag_fresh100 as runner

def main():
 dest=OUT/'full-gold-ranks.json'
 if dest.exists():raise ValueError('do not overwrite')
 old={r['id']:r for r in read(BASE/'cases.json.gz')};selection=read(runner.ROOT/'selection.json')['MTRAG'];cases=[{**c,'query':old[c['id']]['query']} for c in selection]
 original=runner.route_rows;captures=[]
 def capture(selected,ids,dense,lex):
  indexed={c:i for i,c in enumerate(ids)};array=np.array(ids)
  for i,(_,c) in enumerate(selected):
   values=[]
   for gold in sorted({e['document_id'] for e in c['evidence']}):
    j=indexed[gold];row={'gold':gold}
    for name,m in [('dense',dense),('bm25',lex)]:
     score=float(m[i,j]);rank=int(np.count_nonzero(m[i]>score)+np.count_nonzero((m[i]==score)&(array<gold))+1)
     row[name+'_rank']=rank if name=='dense' or score>0 else None
     row[name+'_score']=score
    values.append(row)
   captures.append({'id':c['id'],'query':c['query'],'gold':values})
  routes=original(selected,ids,dense,lex)
  for (_,c),r in zip(selected,routes,strict=True):assert r==old[c['id']]['routes']
  return routes
 runner.route_rows=capture
 try:runner.mtrag(cases)
 finally:runner.route_rows=original
 dest.write_text(json.dumps({'n':len(captures),'api_calls':0,'document_embeddings':0,'query_embeddings':100,'routes20_reproduced':True,'cases':captures},indent=2)+'\n')
 print('Full-domain gold ranks captured; original routes exactly reproduced')

# Rank-depth summary is replayable independently from expensive query encoding.
def summarize():
 ranks=read(OUT/'full-gold-ranks.json');diag={r['id']:r for r in read(OUT/'cases.json.gz')};missing=[]
 for r in ranks['cases']:
  loss={g['gold']:g['first_loss'] for g in diag[r['id']]['gold_trace']}
  for g in r['gold']:
   best=min(g['dense_rank'],g['bm25_rank'] or float('inf'))
   assert (best>20)==(loss[g['gold']]=='union40')
   if best>20:missing.append({**g,'id':r['id'],'best_rank':best})
 report={'missing_instances':len(missing),'best_route_rank_bins':{'21-40':sum(g['best_rank']<=40 for g in missing),'41-100':sum(40<g['best_rank']<=100 for g in missing),'101-1000':sum(100<g['best_rank']<=1000 for g in missing),'>1000':sum(g['best_rank']>1000 for g in missing)},'zero_lexical_gold':sum(g['bm25_rank'] is None for g in missing),'domain_counts':{d:sum(diag[g['id']]['domain']==d for g in missing) for d in ['clapnq','cloud','fiqa','govt']},'top20_identity_reproduced':ranks['routes20_reproduced']}
 (OUT/'rank-depth-summary.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':
 import sys
 if '--summary-only' not in sys.argv:main()
 summarize()
