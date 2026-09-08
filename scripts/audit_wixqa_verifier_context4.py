"""Verify frozen context experiment identities without API calls."""
import json,gzip
from pathlib import Path
from services.claim_verification import fingerprint,make_request
root=Path('artifacts/eval/wixqa-verifier-context4-2026-09-08')
inputs=json.loads(gzip.decompress((root/'inputs.json.gz').read_bytes()))
rows=list(map(json.loads,(root/'results.jsonl').read_text().splitlines()))
assert len(inputs)==len(rows)==4
for a,b in [(0,2),(1,3)]:
    assert inputs[a]['evidence']['knowledge_evidence']==inputs[b]['evidence']['knowledge_evidence']
    assert inputs[a]['answer']==inputs[b]['answer']
assert inputs[1]['answer']==inputs[0]['answer'].split('\n\n')[0]
systems=[]
for i,r in zip(inputs,rows):
    assert r['assessment']['request_hash']==fingerprint(make_request(i['question'],i['answer'],i['evidence']))
    assert len(r['calls'])==1
    systems.append(r['calls'][0]['request']['system'])
assert all(s==systems[0] for s in systems)
summary={'api_calls':4,'same_system':True,'packs_unchanged':True,'request_binding_valid':True,'cases':[{'context':r['context_mode'],'answer':r['answer_mode'],'supported':r['assessment']['supported'],'answered':r['assessment']['answered'],'input_tokens':r['calls'][0]['raw_output']['usage_metadata']['input_tokens']} for r in rows],'adopted':False,'reason':'No semantic detection improvement on selected witness; correctness remains model-judged.'}
(root/'audit.json').write_text(json.dumps(summary,indent=2)+'\n')
print('4 paired inputs and evidence identity checks passed')
