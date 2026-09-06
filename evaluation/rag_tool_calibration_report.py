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
    forbidden_count = 0
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
        ids = [item[0] for item in (row['tool_result'].get('trace') or {}).get('source_ranks',[])][:5]
        packed = (row['tool_result'].get('evidence_pack') or {}).get('items',[])
        # A lower bound: only chunks whose source spans are present in the capture
        # can be scored. When this reaches N/N it proves full Top5 coverage.
        known_fused_count += all(any(e['chunk_id'] in ids and covers(e,g,e['source_ref']) for e in packed) for g in row['gold_evidence'])
        forbidden = sorted({e['source']['source_id'] for e in evidence} & set(row.get('forbidden_sources',[])))
        if 'returned_forbidden_sources' in row and forbidden != row['returned_forbidden_sources']:
            raise ValueError('forbidden source metric drift')
        forbidden_count += bool(forbidden)
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
        'abstentions':sum(bool(r['answer']['abstained']) for r in rows),
        'rerank_fallbacks':sum((r['tool_result'].get('trace') or {}).get('rerank_fallback',False) for r in rows),
        'forbidden_source_returns':forbidden_count,
        'api_calls':len(calls),
        'api_models':dict(Counter(c['request']['model'] for c in calls)),
        'usage':{k:sum(c.get('response',{}).get('usage',{}).get(k,0) or 0 for c in calls) for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')},
        'latency_ms':{'median':statistics.median(latencies),'p95_nearest_rank':latencies[max(0, (95*len(latencies)+99)//100-1)]},
        'semantic_accuracy':'requires source-based review; citation membership alone does not establish support',
    }


def summarize_probe(directory):
    directory = Path(directory)
    capture = directory/'cases.jsonl'
    raw = capture.read_text() if capture.exists() else gzip.decompress((directory/'cases.jsonl.gz').read_bytes()).decode()
    rows = [json.loads(line) for line in raw.splitlines()]
    completion = json.loads((directory/'completion.json').read_text())
    if completion['api_calls'] != 0 or any(r['api_calls'] for r in rows) or completion['cases'] != len(rows):
        raise ValueError('candidate-only capture must have complete cases and zero inference')
    report = {'scope':'request-applicability ablation, same production candidate source, not a deployed before/after comparison', 'cases':len(rows),'api_calls':0,'variants':{}}
    for variant in ('scoped','omitted_applicability'):
        covered, contaminated, details = 0, 0, []
        for row in rows:
            result = row['candidate_probe'][variant]
            candidates = result['candidates']
            complete = all(any(c['source_id']==g['document_id'] and c['source_start_char']<=g['start_char'] and c['source_end_char']>=g['end_char'] and g['quote'] in c['content'] for c in candidates[:5]) for g in row['gold_evidence'])
            wrong = sorted({c['source_id'] for c in candidates} & set(row['forbidden_sources']))
            covered += complete
            contaminated += bool(wrong)
            details.append({'case_id':row['case_id'],'status':result['status'],'complete_evidence_top5':complete,'specified_inapplicable_sources':wrong})
        report['variants'][variant]={'complete_evidence_top5':covered,'requests_with_specified_inapplicable_candidates':contaminated,'cases':details}
    return report


if __name__ == '__main__':
    import sys
    report = (summarize_probe if '--probe' in sys.argv[2:] else summarize)(sys.argv[1])
    (Path(sys.argv[1])/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
