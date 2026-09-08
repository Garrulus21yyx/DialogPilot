"""Frozen resolved-query MTRAG stage attribution and two bounded ablations."""
import json,gzip,hashlib
from pathlib import Path
from collections import defaultdict
from scripts.replay_rag_rank_selection import read,pack
from scripts.audit_rag_fresh100 import data
from scripts.run_rag_fresh100 import ROOT
from mcp.rank_fusion import fuse_rankings
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
OUT=Path('artifacts/eval/mtrag-resolved-diagnosis100-2026-09-08')
BASE=Path('artifacts/eval/rag-query-other200-2026-09-08/MTRAG')
def main():
 OUT.mkdir(parents=True,exist_ok=False)
 selection=read(ROOT/'selection.json');cases={c['id']:c for c in selection['MTRAG']};rows=read(BASE/'cases.json.gz')
 combined=[{**r,'pool':list(dict.fromkeys(c for route in r['routes'].values() for c in route))} for r in rows]
 src,txt,metric,_=data('MTRAG',combined,selection);scores={}
 for s in read(BASE/'scores.json.gz'):
  assert s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest()
  scores[s['id'],s['cid']]=s['score']
 scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4)
 assert scorer.identity==read(ROOT/'MTRAG/identity.json')['reranker']
 manifest={'n':100,'scope':'consumed fixed Flash queries; no agent or generation','source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [BASE/'cases.json.gz',BASE/'scores.json.gz',ROOT/'selection.json']},'new_api_calls':0,'max_new_ce':2000,'reranker':scorer.identity}
 (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 added=[];result=[]
 for r in rows:
  cid=r['id'];c=cases[cid];gold={e['document_id'] for e in c['evidence']};domain=next(x for x in ['clapnq','cloud','fiqa','govt'] if x in c['query_types'])
  pool=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));assert pool==r['pool']
  missing=[d for d in r['routes']['dense'] if (cid,d) not in scores]
  for d,v in zip(missing,scorer._score(r['query'],[txt[d] for d in missing]) if missing else [],strict=True):
   scores[cid,d]=v;added.append({'id':cid,'query':r['query'],'cid':d,'score':v,'text_sha256':hashlib.sha256(txt[d].encode()).hexdigest()})
  assert len(added)<=2000
  ce=sorted(pool,key=lambda d:(-scores[cid,d],d));assert ce==r['ce_order']
  dense_ce=sorted(r['routes']['dense'],key=lambda d:(-scores[cid,d],d))
  rerank_mix=list(fuse_rankings({'ce':ce,'recall':pool},weights={'ce':.75,'recall':.25},rrf_k=10,top_k=20))
  arms={}
  for name,order in [('baseline',ce),('dense20',dense_ce),('ce_recall_mix',rerank_mix)]:
   ids,tokens=pack(r['query'],order,src);arms[name]={**metric(r,ids),'ids':ids,'tokens':tokens}
  assert arms['baseline']==r['model']
  stages={'dense20':r['routes']['dense'],'bm25_20':r['routes']['bm25'],'union40':list(dict.fromkeys(r['routes']['dense']+r['routes']['bm25'])),'fusion20':pool,'ce5':ce[:5],'wire5':arms['baseline']['ids']}
  gold_trace=[]
  for g in sorted(gold):
   ranks={k:(v.index(g)+1 if g in v else None) for k,v in stages.items()}
   loss=next((k for k in ['union40','fusion20','ce5','wire5'] if ranks[k] is None),'retained')
   gold_trace.append({'gold':g,'ranks':ranks,'first_loss':loss})
  result.append({'id':cid,'group':c['group_id'],'domain':domain,'query':r['query'],'gold_trace':gold_trace,'stages':{k:len(set(v)&gold)/len(gold) for k,v in stages.items()},'arms':arms})
 domains={}
 for domain in ['all','clapnq','cloud','fiqa','govt']:
  rr=[r for r in result if domain=='all' or r['domain']==domain]
  if not rr:continue
  domains[domain]={'n':len(rr),'stages':{k:sum(r['stages'][k] for r in rr)/len(rr) for k in rr[0]['stages']},'arms':{a:{k:sum(r['arms'][a][k] for r in rr)/len(rr) for k in ['recall','mrr','ndcg']} for a in rr[0]['arms']}}
 loss=defaultdict(int)
 for r in result:
  for g in r['gold_trace']:loss[g['first_loss']]+=1
 report={'domains':domains,'gold_instance_first_loss':dict(loss),'new_ce_pairs':len(added),'new_api_calls':0,'paired':{a:{'better':sum(r['arms'][a]['recall']>r['arms']['baseline']['recall'] for r in result),'worse':sum(r['arms'][a]['recall']<r['arms']['baseline']['recall'] for r in result)} for a in ['dense20','ce_recall_mix']}}
 for n,v in [('cases',result),('added-scores',added)]: (OUT/(n+'.json.gz')).write_bytes(gzip.compress(json.dumps(v,ensure_ascii=False).encode(),mtime=0))
 (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
