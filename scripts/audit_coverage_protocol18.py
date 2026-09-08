"""Replay protocol aggregation, preserve parsing errors separately from semantic detection."""
import hashlib,json
from pathlib import Path
from evaluation.coverage_protocol_candidate import aggregate
ROOT=Path('artifacts/eval/coverage-protocol18-2026-09-08')
def main():
 manifest=json.loads((ROOT/'manifest.json').read_text());expected={c['id']:c for c in manifest['cases']}
 assert hashlib.sha256(Path('evaluation/coverage_protocol_candidate.py').read_bytes()).hexdigest()==manifest['candidate_source_sha256']
 reports={};total=0
 for file,n in [('development.jsonl',12),('new_validation.jsonl',24),('pro-development.jsonl',6)]:
  rows=[json.loads(l) for l in (ROOT/file).read_text().splitlines()];assert len(rows)==n
  for r in rows:
   assert len(r['calls'])==1;req=r['calls'][0]['request'];assert req['max_retries']==0;total+=1
   assert json.loads(req['messages'][0]['content'])==expected[r['id']]['request']
   if r['arm']!='baseline':
    assert req['system']==manifest['candidate_system']
    try:value=aggregate(expected[r['id']]['request'],r['output'])
    except Exception:assert 'error' in r
    else:assert value==r['result']
  for arm in sorted({r['arm'] for r in rows}):
   rr=[r for r in rows if r['arm']==arm];valid=[r for r in rr if expected[r['id']]['expected_answered'] and expected[r['id']]['expected_supported']];incomplete=[r for r in rr if not expected[r['id']]['expected_answered']];unsupported=[r for r in rr if not expected[r['id']]['expected_supported']]
   reports[file+':'+arm]={'n':len(rr),'protocol_errors':sum('error' in r for r in rr),
    'incomplete_total':len(incomplete),'semantic_omissions_detected':sum(not r['result']['answered'] for r in incomplete if 'result' in r),
    'incomplete_published':sum(r['result']['publishable'] for r in incomplete if 'result' in r),
    'valid_total':len(valid),'valid_rejected_including_protocol':sum(not r.get('result',{}).get('publishable',False) for r in valid),
    'unsupported_total':len(unsupported),'unsupported_published':sum(r['result']['publishable'] for r in unsupported if 'result' in r)}
 assert total==42
 (ROOT/'audit.json').write_text(json.dumps({'calls':total,'reports':reports,'adopt':False},indent=2)+'\n');print(json.dumps(reports,indent=2))
if __name__=='__main__':main()
