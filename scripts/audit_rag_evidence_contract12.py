"""Mechanical source/action audit; semantic assessments remain explicitly manual."""
import hashlib, json
from pathlib import Path
from scripts.evaluate_rag_evidence_contract12 import OUT

def main():
 manifest=json.loads((OUT/'manifest.json').read_text())
 for p,h in manifest['source_hashes'].items(): assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
 rows=[json.loads(l) for l in (OUT/'results.jsonl').read_text().splitlines()]
 assert len(rows)==12 and {r['id'] for r in rows}=={c['id'] for c in manifest['cases']}
 expected={c['id']:c for c in manifest['cases']}; actions={};usage={'input_tokens':0,'output_tokens':0}
 for r in rows:
  assert 'error' not in r and len(r['calls'])==1
  call=r['calls'][0]; req=call['request']; assert req['max_retries']==0
  assert expected[r['id']]['query'] in json.dumps(req['messages'],ensure_ascii=False)
  for k in usage: usage[k]+=call['usage'].get(k,0)
  actions[r['id']]=[c['tool_id'] for c in r['proposal']['commands']]
 for i in ['negative','hypothesis','short','compound','old_assertion','switch','acronym']: assert actions[i]==['knowledge_search']
 for i in ['thanks','missing','generic']: assert actions[i]==[]
 assert actions['lookup']==['refund_status'] and set(actions['mixed'])=={'refund_status','knowledge_search'}
 report={'native_action_selection_matches_authored_expectations':12,'n':12,'usage':usage,'calls':12,'new_business_executions':0,'scope':'mechanical action/source audit, not semantic query or answer correctness'}
 (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
