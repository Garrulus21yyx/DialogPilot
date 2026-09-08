"""Summarize the bounded three-dataset acceptance, without pooling accuracy."""
import gzip,json,hashlib
from pathlib import Path

def main():
    inputs={'Doc2Dial':['artifacts/eval/rag-final-doc2dial2-v3-2026-09-08/0.5'], 'MTRAG':['artifacts/eval/rag-final-mtrag2-v3-2026-09-08'], 'WixQA':['artifacts/eval/wixqa-real-entry-scoped-pair2-v2-2026-09-08/0.5','artifacts/eval/wixqa-real-entry-next2-2026-09-08/0.5']}
    datasets={}
    for name,paths in inputs.items():
        records=[]
        for path in paths:
            p=Path(path)/'full-cases.jsonl.gz'
            for r in map(json.loads,gzip.decompress(p.read_bytes()).decode().splitlines()):
                response=r.get('outcome',{}).get('response',{})
                records.append({'case_id':r['case_id'],'source':str(p),'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'runtime_outcome':r.get('outcome_type',r.get('error_type')),'tool_calls':len(r['tools']),'api_calls':len(r['api_calls']),'query':[t['params'] for t in r['tools']],'answer':response.get('response'),'verified':response.get('verified'),'latency_ms':response.get('latency_ms')})
        datasets[name]={'case_count':len(records),'api_calls':sum(r['api_calls'] for r in records),'records':records}
    report={'datasets':datasets,'selected_final_arm_api_calls':sum(d['api_calls'] for d in datasets.values()),'mtrag_adapter_failed_attempt_api_calls':6,'limits':'8 consumed development examples; descriptive acceptance, not a pooled accuracy estimate or independent heldout test. MTRAG uses full Cloud local reference index, others use PG. Runs have captured distinct code identities; not one frozen release certification.'}
    p=Path('artifacts/eval/rag-three-dataset-final-entry-2026-09-08');p.mkdir(exist_ok=True)
    (p/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print({k:(v['case_count'],v['api_calls']) for k,v in datasets.items()})
if __name__=='__main__':main()
