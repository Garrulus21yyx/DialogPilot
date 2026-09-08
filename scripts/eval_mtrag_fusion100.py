"""Two preregistered fusion repairs on frozen rankings; no query/model selection."""
import gzip,json,hashlib
from pathlib import Path
from collections import defaultdict, Counter
import numpy as np
from scripts.eval_mtrag_depth40 import OUT as DEPTH,BASE,PREV
from scripts.audit_rag_fresh100 import data
from scripts.replay_rag_rank_selection import read,digest,pack
from scripts.run_rag_fresh100 import ROOT
from mcp.rank_fusion import fuse_rankings
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
OUT=Path('artifacts/eval/mtrag-fusion100-2026-09-08')
def save(n,v):
 b=json.dumps(v,ensure_ascii=False,indent=2).encode();(OUT/n).write_bytes(gzip.compress(b,mtime=0) if n.endswith('.gz') else b+b'\n')
def pools(routes):
 equal=fuse_rankings(routes,weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20)
 quota=list(dict.fromkeys(routes['dense'][:10]+routes['bm25'][:10]+equal))[:20]
 return {'equal':equal,'dense75':fuse_rankings(routes,weights={'dense':.75,'bm25':.25},rrf_k=10,top_k=20),'quota10':quota}
def detail(cid,routes):
 ranks={k:v.index(cid)+1 if cid in v else None for k,v in routes.items()}
 return {'id':cid,'ranks':ranks,'rrf_equal':sum(.5/(10+r) for r in ranks.values() if r is not None)}
def main():
 OUT.mkdir(exist_ok=False);rows=read(DEPTH/'cases.json.gz');old={r['id']:r for r in read(BASE/'cases.json.gz')};sel=read(ROOT/'selection.json');byid={c['id']:c for c in sel['MTRAG']}
 paths=[DEPTH/'cases.json.gz',BASE/'cases.json.gz',BASE/'scores.json.gz',PREV/'added-scores.json.gz',DEPTH/'added-scores.json.gz',ROOT/'selection.json']
 save('manifest.json',{'gap':'R04/R05','n':100,'split':'consumed development','api_calls':0,'embedding_calls':0,'max_new_ce':4000,'strategies':['equal','dense75','quota10'],'budget':{'ce':20,'wire':5,'tokens':2600},'source_hashes':{str(p):digest(p) for p in paths}})
 src,txt,metric,_=data('MTRAG',[{**r,'pool':list(set(sum(r['routes'].values(),[])))} for r in rows],sel);scores={}
 for p in paths[2:5]:
  for s in read(p):
   if s['cid'] not in txt:continue
   assert s['query']==old[s['id']]['query'] and s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest()
   scores[s['id'],s['cid']]=s['score']
 scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4);assert scorer.identity==read(DEPTH/'identity.json')['reranker'];save('identity.json',scorer.identity)
 added=[];result=[]
 for r in rows:
  pp=pools(r['routes']);assert pp['equal']==r['pool'];gold={e['document_id'] for e in byid[r['id']]['evidence']}
  missing=sorted({d for p in pp.values() for d in p if (r['id'],d) not in scores})
  for d,v in zip(missing,scorer._score(r['query'],[txt[d] for d in missing]) if missing else [],strict=True):
   scores[r['id'],d]=v;added.append({'id':r['id'],'query':r['query'],'cid':d,'score':v,'text_sha256':hashlib.sha256(txt[d].encode()).hexdigest()})
  assert len(added)<=4000
  arms={}
  for a,p in pp.items():
   order=sorted(p,key=lambda d:(-scores[r['id'],d],d));ids,tokens=pack(r['query'],order,src)
   arms[a]={'pool':p,'ce_order':order,'candidate_recall':len(set(p)&gold)/len(gold),'wire':{**metric(r,ids),'ids':ids,'tokens':tokens}}
  assert arms['equal']['wire']==r['candidate']
  traces=[{**detail(g,r['routes']),'old_pool_rank':old[r['id']]['pool'].index(g)+1 if g in old[r['id']]['pool'] else None,'new_pool_ranks':{a:p.index(g)+1 if g in p else None for a,p in pp.items()}} for g in sorted(gold)]
  change={k:[detail(d,r['routes']) for d in sorted(v)] for k,v in {'entered':set(r['pool'])-set(old[r['id']]['pool']),'exited':set(old[r['id']]['pool'])-set(r['pool'])}.items()}
  result.append({'id':r['id'],'group':r['group'],'domain':r['domain'],'query':r['query'],'routes':r['routes'],'original':r['baseline'],'arms':arms,'gold_trace':traces,'depth_change':change})
  save('cases.json.gz',result);save('added-scores.json.gz',added)
 report={'n':100,'api_calls':0,'new_ce_pairs':len(added),'domains':{}}
 for domain in ['all','clapnq','cloud','fiqa','govt']:
  rr=[r for r in result if domain=='all' or r['domain']==domain];out={}
  for a in pp:
   out[a]={'candidate_recall':sum(r['arms'][a]['candidate_recall'] for r in rr)/len(rr),**{k:sum(r['arms'][a]['wire'][k] for r in rr)/len(rr) for k in ['recall','mrr','ndcg']},'better':sum(r['arms'][a]['wire']['recall']>r['original']['recall'] for r in rr),'worse':sum(r['arms'][a]['wire']['recall']<r['original']['recall'] for r in rr)}
  report['domains'][domain]={'n':len(rr),'arms':out}
 report['paired']={}
 for a in ['dense75','quota10']:
  report['paired'][a]={}
  for base in ['original','equal']:
   groups=defaultdict(list)
   for r in result:groups[r['group']].append(r['arms'][a]['wire']['recall']-(r['original']['recall'] if base=='original' else r['arms']['equal']['wire']['recall']))
   sums=np.array([sum(v) for v in groups.values()]);counts=np.array([len(v) for v in groups.values()]);ix=np.random.default_rng(20260908).integers(0,len(groups),(20000,len(groups)));d=sums[ix].sum(1)/counts[ix].sum(1)
   report['paired'][a][base]={'delta':sums.sum()/100,'groups':len(groups),'ci95':np.quantile(d,[.025,.975]).tolist()}
 movement={}
 for a in ['dense75','quota10']:
  counts=Counter()
  for r in result:
   gold={g['id'] for g in r['gold_trace']};b=r['arms']['equal'];v=r['arms'][a]
   counts['pool_gold_gained']+=len(gold&set(v['pool'])-set(b['pool']));counts['pool_gold_lost']+=len(gold&set(b['pool'])-set(v['pool']))
   lost=gold&set(b['wire']['ids'])-set(v['wire']['ids']);counts['wire_gold_lost']+=len(lost);counts['wire_gold_gained']+=len(gold&set(v['wire']['ids'])-set(b['wire']['ids']))
   for g in lost:counts['lost_before_ce' if g not in v['pool'] else 'lost_ce5' if g not in v['ce_order'][:5] else 'lost_pack']+=1
  movement[a]=dict(counts)
 save('movement.json',movement)
 save('report.json',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
