"""Source and article-qrel audit of scoped public real-entry outputs."""
import json,re,hashlib,gzip,argparse
from pathlib import Path

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('artifacts/eval/wixqa-real-entry-scoped-pair2-v2-2026-09-08'));root=parser.parse_args().root
 with Path('/tmp/dialogpilot-rag-external-lock-20260907/wix-corpus.jsonl').open() as f:docs={str(r['id']):r['contents'] for r in map(json.loads,f)}
 result={};identities=[]
 for arm in ('0.25','0.5'):
  p=root/arm;identities.append(json.loads((p/'full-manifest.json').read_text())['source_sha256']);gold=[set(c['article_ids']) for c in json.loads((p/'selection.json').read_text())['cases']];items=[]
  routes=json.loads(gzip.decompress((p/'source-route-captures.json.gz').read_bytes()))
  case_path=p/'full-cases.jsonl'
  case_text=case_path.read_text() if case_path.exists() else gzip.decompress((p/'full-cases.jsonl.gz').read_bytes()).decode()
  for i,line in enumerate(case_text.splitlines()):
   r=json.loads(line);answer=r['outcome']['response']['response'];t=r['tools'][0];pack=t['result']['data']['evidence_pack'];sources=set()
   assert pack['retrieval_policy']['vector_weight']==float(arm)
   for e in pack['items']:
    s=e['source_ref'];text=docs[s['source_id']];assert text[s['start_char']:s['end_char']]==e['text'];assert hashlib.sha256(text.encode()).hexdigest()==s['checksum'];sources.add(s['source_id'])
   wire=json.loads(t['result']['output_for_model']);evidence={e['evidence_id']:e for e in wire['evidence']};refs=re.findall(r'\[(E[^\]]+)\]',answer);assert refs and all(ref in evidence for ref in refs)
   candidate_sources={c['source_id'] for c in routes[i]['fused_candidates']}
   items.append({'candidate_article_recall':len(candidate_sources&gold[i])/len(gold[i]),'query':t['params'],'answer':answer,'pack_article_recall':len(sources&gold[i])/len(gold[i]),'cited_evidence':{ref:evidence[ref] for ref in refs},'api_calls':len(r['api_calls']),'source_and_reference_checks':True})
  result[arm]=items
 assert identities[0]==identities[1]
 report={'arms':result,'same_source_identity':True,'total_api_calls':sum(x['api_calls'] for v in result.values() for x in v),'scope':'2 existing dev questions; source and ID correctness do not prove semantic support; queries generated independently.'}
 (root/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print('API calls',report['total_api_calls']);print('pack article recall',{k:[v['pack_article_recall'] for v in vs] for k,vs in result.items()})
if __name__=='__main__':main()
