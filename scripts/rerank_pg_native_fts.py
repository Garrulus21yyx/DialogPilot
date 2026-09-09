"""Shared pointwise CE scores and production packer on frozen lexical alternatives."""
import argparse,gzip,json,subprocess,math
from pathlib import Path
from urllib.parse import quote
import psycopg
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from application.knowledge_retrieval_text import build_child_retrieval_text
from mcp.context_packer import ContextCandidate,ContextPacker
from scripts.run_wixqa_fixed_comparison import measure
from scripts.prepare_wixqa_local_index import digest
from statistics import mean

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);a=p.parse_args();m=json.loads((a.input/'manifest.json').read_text());rows=json.loads((a.input/'cases.json').read_text())
 cache=Path('/tmp/dialogpilot-wixqa-full-index-20260908');complete=json.loads((cache/'COMPLETE.json').read_text());assert digest(cache/'chunks.json.gz')==complete['chunks_sha256']
 chunks=json.loads(gzip.decompress((cache/'chunks.json.gz').read_bytes()));bykey={f"{x['source_id']}:{x['start_char']}:{x['end_char']}":x for x in chunks}
 cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];e=dict(v.split('=',1) for v in cfg['Config']['Env'] if '=' in v)
 url='postgresql://'+quote(e['POSTGRES_USER'],safe='')+':'+quote(e['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'+m['database']
 with psycopg.connect(url,autocommit=True) as c:
  c.execute('SET default_transaction_read_only=on')
  mapping=c.execute('select candidate_id,source_id,source_span from retrieval.knowledge_chunk_search where generation_id=%s',(m['generation'],)).fetchall()
 source={cid:bykey[f"{sid}:{span['start_char']}:{span['end_char']}"] for cid,sid,span in mapping}
 reranker=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4)
 oldroot=Path('artifacts/eval/wixqa-fixed-dev20-2026-09-08');identity=json.loads((oldroot/'identity.json').read_text());assert identity['reranker']==reranker.identity;assert identity['index']==complete
 old={r['case']['query']:r for r in (json.loads(l) for l in gzip.open(oldroot/'cases.jsonl.gz','rt'))}
 out=[];new=0;reused=0
 for i,r in enumerate(rows):
  q=r['case']['query'];union=list(dict.fromkeys(cid for ids in r['fused'].values() for cid in ids));scores={}
  for cid in union:
   item=source[cid];key=f"{item['source_id']}:{item['start_char']}:{item['end_char']}"
   if key in old[q]['scores']:scores[cid]=old[q]['scores'][key];reused+=1
  missing=[x for x in union if x not in scores]
  values=reranker._score(q,[build_child_retrieval_text(title=source[x]['title'],section_path=(),content=source[x]['text']) for x in missing]) if missing else []
  scores.update(zip(missing,values,strict=True));new+=len(missing);assert all(math.isfinite(v) for v in scores.values())
  arms={}
  for arm,ids in r['fused'].items():
   ordered=sorted(ids,key=lambda x:(-scores[x],ids.index(x)))
   candidates=[ContextCandidate(chunk_id=x,document_id=source[x]['source_id'],text=source[x]['text'],start_char=source[x]['start_char'],end_char=source[x]['end_char'],title=source[x]['title'],source_checksum=source[x]['source_checksum'],source_revision=m['generation']) for x in ordered]
   pack=ContextPacker().pack(candidates,max_tokens=2600,max_chunks=5)
   arms[arm]={'ce5':measure(ordered[:5],source,set(r['case']['article_ids']),5),'pack5':measure(pack.chunk_ids,source,set(r['case']['article_ids']),5),'ordered':ordered,'packed':list(pack.chunk_ids)}
  out.append({'case_id':r['case']['group_id'],'scores':scores,'arms':arms});(a.input/'rerank-cases.json').write_text(json.dumps(out,indent=2)+'\n');print(i+1,new,reused,flush=True)
 report={'api_calls':0,'new_ce_pairs':new,'reused_ce_pairs':reused,'identity':reranker.identity,'scope':'local CE and ContextPacker only, not ToolMessage or answers','summary':{arm:{stage:{k:mean(r['arms'][arm][stage][k] for r in out) for k in out[0]['arms'][arm][stage]} for stage in ('ce5','pack5')} for arm in arms}}
 (a.input/'rerank-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
