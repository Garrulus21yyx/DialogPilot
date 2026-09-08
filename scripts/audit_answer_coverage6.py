"""Report observed labels against frozen expectations, including invalid interventions."""
import json
from pathlib import Path
ROOT=Path('artifacts/eval/answer-coverage6-2026-09-08')
def main():
 manifest=json.loads((ROOT/'manifest.json').read_text());expected={c['id']:c['expected_answered'] for c in manifest['cases']};summary={};total=0
 for file in ['results.jsonl','composition.jsonl','composition-v2.jsonl','decomposition.jsonl']:
  rows=[json.loads(l) for l in (ROOT/file).read_text().splitlines()]
  for r in rows:
   assert 'error' not in r
   total+=len(r['calls'])
   for c in r['calls']:assert c['request']['max_retries']==0
  if file=='results.jsonl':
   for arm in ['baseline','candidate']:
    rr=[r for r in rows if r['arm']==arm];assert len(rr)==6
    for r in rr:
     assert (manifest['candidate_addition'] in r['calls'][0]['request']['system']) == (arm=='candidate')
    summary[arm]={'incomplete_detected':sum(not r['assessment']['answered'] for r in rr if not expected[r['id']]),'incomplete_total':3,'valid_false_rejections':sum(not r['assessment']['answered'] for r in rr if expected[r['id']]),'valid_total':3}
  elif file=='decomposition.jsonl':
   summary['decomposition']={'incomplete_detected':sum(not r['answered'] for r in rows if not expected[r['id']]),'incomplete_total':3,'valid_false_rejections':sum(not r['answered'] for r in rows if expected[r['id']]),'valid_total':3}
  else:
   for r in rows: assert (manifest['candidate_addition'] in r['calls'][0]['request']['system'])==(file=='composition-v2.jsonl')
 summary['total_calls_including_invalid_intervention']=total;assert total==26
 summary['adoption']='none: original invoice remains missed in all verifier variants; composition candidate does not explain the gap'
 (ROOT/'audit.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
