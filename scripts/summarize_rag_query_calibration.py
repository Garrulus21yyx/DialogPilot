"""Pair actual Agent arms; raw short-query comparator remains diagnostic only."""
import gzip, json
from pathlib import Path
from infrastructure.target_model_context import planning_payload_from_request

def main():
    base=Path('artifacts/eval/rag-query-calibration12-2026-09-08');cand=Path('artifacts/eval/rag-query-contract-candidate12-2026-09-08')
    a=json.loads((base/'audit.json').read_text());b=json.loads((cand/'audit.json').read_text())
    qa=[json.loads(l) for l in gzip.decompress((base/'queries.jsonl.gz').read_bytes()).decode().splitlines()];qb=[json.loads(l) for l in gzip.decompress((cand/'queries.jsonl.gz').read_bytes()).decode().splitlines()]
    assert [r['case_id'] for r in a['rows']]==[r['case_id'] for r in b['rows']]
    assert all(x['arms']['raw']==y['arms']['raw'] for x,y in zip(a['rows'],b['rows'],strict=True))
    for x,y in zip(qa,qb,strict=True):
        assert planning_payload_from_request(x['calls'][0]['request'])==planning_payload_from_request(y['calls'][0]['request'])
        assert x['calls'][0]['request']['tools']==y['calls'][0]['request']['tools']
        assert x['calls'][0]['request']['model']==y['calls'][0]['request']['model']
    reviews=[
        'Both clarify unidentified complaint information; no retrieval is not automatically wrong.',
        'Candidate preserves NY/out-of-state inspection; baseline offers lookup but only asks more questions.',
        'Both ask what extension is needed despite inspection expiry in history; no evidence delivered.',
        'Candidate retrieves; baseline states inspection policy without evidence and asks whether to look it up.',
        'Candidate retrieves inspection contents with NY/private seller context; baseline asks inspection type.',
        'Both ask jurisdiction/type; applicability may need clarification, zero delivery is not enough to label answer wrong.',
        'Current inspection question is entangled with old extension topic; clarification appropriateness needs independent review.',
        'Candidate returns empty structured arguments, INVALID_PROVIDER_OUTPUT; no retry.',
        'Both interpret do not as refusal rather than negative answer; ambiguous source phrasing remains a witness.',
        'Candidate retrieves expired inspection penalty; baseline answers without retrieved evidence.',
        'Candidate retrieves unexpired sticker replacement; baseline asserts no replacement needed without supporting retrieval.',
        'Both preserve I-PIRP sponsor cancellation and retrieve complete evidence.']
    rows=[{'case_id':x['case_id'],'current_query':x['raw_query'],'baseline':x['arms']['resolved'],'candidate':y['arms']['resolved'],
           'baseline_disposition':x['planning_disposition'],'candidate_disposition':y['planning_disposition'],'author_review':note}
          for x,y,note in zip(a['rows'],b['rows'],reviews,strict=True)]
    summary={'n':12,'api_calls':24,'source_payloads_and_schemas_identical':True,'raw_retrieval_identical':True,
             'baseline':a['arms']['resolved'],'candidate':b['arms']['resolved'],
             'rescued':sum(not x['baseline']['visible_complete'] and x['candidate']['visible_complete'] for x in rows),
             'lost':sum(x['baseline']['visible_complete'] and not x['candidate']['visible_complete'] for x in rows),
             'rows':rows,'adopted':False,'reason':'Development improvement but semantic/protocol/generalization gates not satisfied; no production prompt change.',
             'usage':{arm:{key:sum(q['calls'][0].get('usage',{}).get(key,0) for q in qs) for key in ('input_tokens','output_tokens')} for arm,qs in [('baseline',qa),('candidate',qb)]}}
    (cand/'paired-report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
