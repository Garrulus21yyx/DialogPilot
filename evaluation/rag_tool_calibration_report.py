"""Recompute fixed-query calibration evidence and costs from captured artifacts."""
import json
import gzip
from pathlib import Path
from collections import Counter
import statistics


def summarize(directory):
    directory = Path(directory)
    capture = directory/'cases.jsonl'
    raw = capture.read_text() if capture.exists() else gzip.decompress((directory/'cases.jsonl.gz').read_bytes()).decode()
    rows = [json.loads(line) for line in raw.splitlines()]
    complete = json.loads((directory/'completion.json').read_text())
    if len(rows) != complete['cases'] or len({r['case_id'] for r in rows}) != len(rows):
        raise ValueError('case count or uniqueness drift')
    calls = [c for r in rows for c in r['api_calls']]
    if len(calls) != complete['api_calls']:
        raise ValueError('call count drift')
    visible_count = 0
    known_fused_count = 0
    citation_count = 0
    for row in rows:
        evidence = row['tool_message'].get('evidence', [])
        def covers(item, gold, ref):
            return (ref['source_id'] == gold['document_id']
                    and ref['start_char'] <= gold['start_char']
                    and ref['end_char'] >= gold['end_char']
                    and gold['quote'] in item['text'])
        visible = all(any(covers(e, g, e['source']) for e in evidence) for g in row['gold_evidence'])
        if visible != row['complete_visible_evidence']:
            raise ValueError('visible evidence metric drift')
        visible_count += visible
        ids = [item[0] for item in row['tool_result']['trace']['source_ranks']][:5]
        packed = row['tool_result']['evidence_pack']['items']
        # A lower bound: only chunks whose source spans are present in the capture
        # can be scored. When this reaches N/N it proves full Top5 coverage.
        known_fused_count += all(any(e['chunk_id'] in ids and covers(e,g,e['source_ref']) for e in packed) for g in row['gold_evidence'])
        available = {e['evidence_id'] for e in evidence}
        citation_count += all(set(c['citations']) <= available and c['citations'] for c in row['answer']['claims'])
    latencies = sorted(r['latency_ms'] for r in rows)
    return {
        'scope':'fixed-query knowledge handler + model-visible evidence + grounded generator; not agent/business E2E',
        'cases':len(rows), 'complete_visible_evidence':visible_count,
        'known_fused_top5_complete_evidence_lower_bound':known_fused_count,
        'answer_claim_citation_membership':citation_count,
        'retrieval_status':dict(Counter(r['tool_result']['status'] for r in rows)),
        'answer_errors':sum(bool(r['answer']['error']) for r in rows),
        'rerank_fallbacks':sum(r['tool_result']['trace']['rerank_fallback'] for r in rows),
        'api_calls':len(calls),
        'api_models':dict(Counter(c['request']['model'] for c in calls)),
        'usage':{k:sum(c.get('response',{}).get('usage',{}).get(k,0) or 0 for c in calls) for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')},
        'latency_ms':{'median':statistics.median(latencies),'p95_nearest_rank':latencies[max(0, (95*len(latencies)+99)//100-1)]},
        'semantic_accuracy':'requires source-based review; citation membership alone does not establish support',
    }


if __name__ == '__main__':
    import sys
    report = summarize(sys.argv[1])
    (Path(sys.argv[1])/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
