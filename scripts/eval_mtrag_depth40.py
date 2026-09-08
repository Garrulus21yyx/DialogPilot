"""Fixed-query route-depth ablation; reuse scores, keep CE20 and wire5/2600."""
import gzip,json,hashlib,time
from pathlib import Path
from scripts.replay_rag_rank_selection import read,pack
import scripts.run_rag_fresh100 as runner
from scripts.diagnose_mtrag_resolved100 import BASE,OUT as PREV
from mcp.rank_fusion import fuse_rankings
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
OUT=Path('artifacts/eval/mtrag-depth40-2026-09-08')
def save(name,value):
 p=OUT/name;b=json.dumps(value,ensure_ascii=False,indent=2).encode();p.write_bytes(gzip.compress(b,mtime=0) if name.endswith('.gz') else b+b'\n')
def main():
 OUT.mkdir(exist_ok=False);start=time.monotonic()
 old={r['id']:r for r in read(BASE/'cases.json.gz')};sel=read(runner.ROOT/'selection.json')['MTRAG'];cases=[{**c,'query':old[c['id']]['query']} for c in sel]
 save('manifest.json',{'gap':'R04/R05','hypothesis':'route40 improves final recall under CE20/wire5/2600','n':100,'split':'consumed development','api_calls':0,'max_new_ce':2000,'query_embeddings':100,'document_embeddings':0,'weights':[.5,.5],'rrf_k':10,'source_hashes':{str(p):runner.digest(p) for p in [BASE/'cases.json.gz',BASE/'scores.json.gz',PREV/'added-scores.json.gz',runner.ROOT/'selection.json']}})
 original=runner.route_rows
 def routes(selected,ids,dense,lex):
  prior=original(selected,ids,dense,lex);result=[]
  for i,(_,c) in enumerate(selected):
   r={'dense':list(runner.ranked(dense[i],ids,40)),'bm25':[ids[j] for j in sorted(range(len(ids)),key=lambda j:(-float(lex[i,j]),ids[j])) if lex[i,j]>0][:40]}
   assert prior[i]==old[c['id']]['routes']=={k:v[:20] for k,v in r.items()};result.append(r)
  return result
 runner.route_rows=routes
 try:rows,src,txt,metric,identity=runner.mtrag(cases)
 finally:runner.route_rows=original
 scores={}
 for p in [BASE/'scores.json.gz',PREV/'added-scores.json.gz']:
  for s in read(p):
   if s['cid'] not in txt:continue
   assert s['query']==old[s['id']]['query'] and s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest()
   scores[s['id'],s['cid']]=s['score']
 scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4)
 assert scorer.identity==read(runner.ROOT/'MTRAG/identity.json')['reranker'];save('identity.json',{'corpus':identity,'reranker':scorer.identity})
 added=[];results=[]
 for r in rows:
  pool=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));missing=[d for d in pool if (r['id'],d) not in scores]
  for d,v in zip(missing,scorer._score(r['query'],[txt[d] for d in missing]) if missing else [],strict=True):
   scores[r['id'],d]=v;added.append({'id':r['id'],'query':r['query'],'cid':d,'score':v,'text_sha256':hashlib.sha256(txt[d].encode()).hexdigest()})
  assert len(added)<=2000
  base=old[r['id']];ids,tokens=pack(r['query'],base['ce_order'],src);assert {**metric(r,ids),'ids':ids,'tokens':tokens}==base['model']
  order=sorted(pool,key=lambda d:(-scores[r['id'],d],d));ids,tokens=pack(r['query'],order,src)
  stages={'union':list(set(sum(r['routes'].values(),[]))),'fusion20':pool,'ce5':order[:5],'wire5':ids}
  results.append({'id':r['id'],'group':r['group'],'query':r['query'],'domain':next(c for c in ['clapnq','cloud','fiqa','govt'] if c in next(s for s in sel if s['id']==r['id'])['query_types']),'routes':r['routes'],'pool':pool,'ce_order':order,'baseline':base['model'],'candidate':{**metric(r,ids),'ids':ids,'tokens':tokens},'stages':{k:len(set(v)&r['gold'])/len(r['gold']) for k,v in stages.items()}})
  save('added-scores.json.gz',added);save('cases.json.gz',results)
 report={'n':len(results),'new_ce_pairs':len(added),'api_calls':0,'elapsed_seconds':time.monotonic()-start,'domains':{}}
 for domain in ['all','clapnq','cloud','fiqa','govt']:
  rr=[r for r in results if domain=='all' or r['domain']==domain]
  report['domains'][domain]={'n':len(rr),'arms':{a:{k:sum(r[a][k] for r in rr)/len(rr) for k in ['recall','mrr','ndcg']} for a in ['baseline','candidate']},'stages':{k:sum(r['stages'][k] for r in rr)/len(rr) for k in rr[0]['stages']},'better':sum(r['candidate']['recall']>r['baseline']['recall'] for r in rr),'worse':sum(r['candidate']['recall']<r['baseline']['recall'] for r in rr)}
 save('report.json',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
