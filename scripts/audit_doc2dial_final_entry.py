"""Audit real multi-turn entry, source spans and visible evidence; no API."""
import gzip,json,hashlib,re
from pathlib import Path
ROOT=Path('artifacts/eval/rag-final-doc2dial2-v3-2026-09-08/0.5')
def main():
    snapshot_path=Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz')
    assert hashlib.sha256(snapshot_path.read_bytes()).hexdigest()==(ROOT/'source-snapshot-sha256.txt').read_text().strip()
    s=json.loads(gzip.decompress(snapshot_path.read_bytes()))['doc2dial-rag-mini-dev-v1']
    docs={d['id']:d['content'] for d in map(json.loads,s['corpus.jsonl'].splitlines())}
    cases=json.loads((ROOT/'selection.json').read_text())['cases']
    rows=[json.loads(l) for l in gzip.decompress((ROOT/'full-cases.jsonl.gz').read_bytes()).decode().splitlines()]
    summary=[]
    for c,r in zip(cases,rows,strict=True):
        planner=json.dumps(r['api_calls'][0]['request'],ensure_ascii=False)
        assert all(v in planner for v in c['history'])
        answer=r['outcome']['response']['response'];visible=[]
        for t in r['tools']:
            pack=t['result']['data']['evidence_pack']
            for e in pack['items']:
                ref=e['source_ref'];source=docs[ref['source_id']]
                assert source[ref['start_char']:ref['end_char']]==e['text']
                assert hashlib.sha256(source.encode()).hexdigest()==ref['checksum']
            wire=json.loads(t['result']['output_for_model']);visible.extend(wire['evidence'])
        refs=re.findall(r'\[(E[^\]]+)\]',answer)
        assert all(ref in {e['evidence_id'] for e in visible} for ref in refs)
        covered=[any(e['source']['source_id']==g['document_id'] and e['source']['start_char']<=g['start_char'] and e['source']['end_char']>=g['end_char'] for e in visible) for g in c['evidence']]
        summary.append({'case_id':c['id'],'history_present_in_actual_planner':True,'tool_calls':len(r['tools']),'api_calls':len(r['api_calls']),'visible_gold_span_recall':sum(covered)/len(covered),'answer':answer,'outcome_type':r['outcome_type']})
    (ROOT/'audit.json').write_text(json.dumps({'cases':summary,'source_checks':True,'total_api_calls':sum(r['api_calls'] for r in summary),'quality':'Author assessment: first response wrong language and unspecific clarification; second preserves required temporal condition. No independent blind grading.'},indent=2,ensure_ascii=False)+'\n')
    print('history/source/citation checks passed; API',sum(r['api_calls'] for r in summary))
if __name__=='__main__':main()
