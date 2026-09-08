"""Audit two real-entry arms without conflating model verification and accuracy."""
import json,re,hashlib
from pathlib import Path
from evaluation.rag_ecommerce_dev import synthetic_development
from evaluation.rag_applicability_dev import applicability_development


def main():
 root=Path('artifacts/eval/rag-real-weight-pair2-2026-09-08')
 docs={d.document_id:d.content for d in synthetic_development()[0]+applicability_development()[0]}
 sources=[];out={}
 for arm,weight in [('base',.25),('balanced',.5)]:
  sources.append(json.loads((root/arm/'source-identity.json').read_text())['files']);items=[]
  for line in (root/arm/'run/full-cases.jsonl').read_text().splitlines():
   row=json.loads(line);answer=row['outcome']['response']['response'];tool=row['tools'][0];pack=tool['result']['data']['evidence_pack']
   assert pack['retrieval_policy']['vector_weight']==weight
   assert pack['retrieval_policy']['lexical_weight']==1-weight
   for item in pack['items']:
    ref=item['source_ref'];original=docs[ref['source_id']]
    assert original[ref['start_char']:ref['end_char']]==item['text']
    assert hashlib.sha256(original.encode()).hexdigest()==ref['checksum']
   wire=json.loads(tool['result']['output_for_model']);evidence={e['evidence_id']:e for e in wire['evidence']}
   refs=re.findall(r'\[(E[^\]]+)\]',answer);assert refs and all(ref in evidence for ref in refs)
   items.append({'case_id':row['case_id'],'query':tool['params'],'answer':answer,'cited_evidence':{r:evidence[r] for r in refs},'source_intervals_exact':True,'runtime_completed':row['outcome_type']=='Completed','api_calls':len(row['api_calls'])})
  out[arm]=items
 assert sources[0]==sources[1]
 result={'api_calls':sum(r['api_calls'] for rs in out.values() for r in rs),'source_identity_equal':True,'queries_identical':all(a['query']==b['query'] for a,b in zip(out['base'],out['balanced'],strict=True)),'arms':out,'semantic_review':'Codex nonblind: required core conclusions supported by cited policies; conditional quality-aftercare addition is sourced but unnecessary for known non-quality request. This is not a blinded accuracy metric.'}
 (root/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='arms'},ensure_ascii=False))

if __name__=='__main__':main()
