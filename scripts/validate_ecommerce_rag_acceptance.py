"""Offline split/provenance/contract checks; does not grade natural language answers."""
import json,hashlib
from scripts.build_ecommerce_rag_acceptance import ROOT

def main():
 read=lambda n:json.loads((ROOT/n).read_text());m=read('manifest.json')
 for n,h in m['sha256'].items():assert hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h,n
 corpus={d['source_id']:d for d in read('corpus.json')};groups={};counts={}
 for split,expected in [('dev',40),('heldout',80)]:
  rows=read(split+'.inputs.json');gold={r['id']:r for r in read(split+'.gold.json')};fx={r['id']:r for r in read(split+'.fixtures.json')};assert len(rows)==len(gold)==len(fx)==expected
  assert len({r['id'] for r in rows})==expected;groups[split]={r['group_id'] for r in rows}
  for r in rows:
   assert set(r)=={'id','group_id','category','history','message'}
   assert r['group_id']==gold[r['id']]['group_id']
   assert all(role in ('user','assistant') and isinstance(text,str) and text for role,text in r['history'])
   for sp in gold[r['id']]['source_spans']:assert corpus[sp['source_id']]['content'][sp['start']:sp['end']]==sp['quote']
   assert bool(fx[r['id']]['orders'])==gold[r['id']]['business_required']
   assert all(o['status'] in ('SHIPPED','PAID') for o in fx[r['id']]['orders'])
  counts[split]=len(rows)
 assert groups['dev'].isdisjoint(groups['heldout'])
 report={'valid':True,'cases':counts,'groups':{k:len(v) for k,v in groups.items()},'api_calls':0,'semantic_gold_independently_reviewed':False,'runtime_executed':False}
 (ROOT/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
if __name__=='__main__':main()
