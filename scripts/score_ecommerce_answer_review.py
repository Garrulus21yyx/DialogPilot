"""Audit frozen answer inputs and aggregate explicit assistant-authored judgments (no model)."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root):
    rows = [json.loads(line) for line in (root / 'answers.jsonl').read_text().splitlines()]
    reviews = json.loads((root / 'semantic-review.json').read_text())['cases']
    gold = {r['id']: r for r in json.loads(Path('data/eval/ecommerce-complex-v2/heldout.gold.json').read_text())}
    inputs = {r['id']: r for r in json.loads(Path('data/eval/ecommerce-complex-v2/heldout.inputs.json').read_text())}
    assert len(rows) == len(reviews) == len(gold) == 80
    assert len({r['id'] for r in rows}) == 80
    assert len({r['id'] for r in reviews}) == 80
    assert {r['id'] for r in rows} == {r['id'] for r in reviews} == set(gold)
    reviewed = {r['id']: r for r in reviews}
    compact, calls, outcomes, categories = [], [], Counter(), {}
    valid_citations = total_citations = complete_evidence = checked_prompts = 0
    for r in rows:
        item, judgment, expected = gold[r['id']], reviewed[r['id']], inputs[r['id']]
        assert r['question'] == expected['message'] and r['history'] == expected['history']
        evidence = r['wire']['evidence']
        visible = [any(u['quote'] in e['text'] for e in evidence) for u in item['evidence_units']]
        complete_evidence += all(visible)
        a = r['answer']
        assert len(judgment['required_units_fully_covered']) == 3
        if a is None:
            assert not r['calls'] and r['retrieval_status'] != 'OK'
            outcome = 'upstream_failure'
        else:
            assert r['calls'] and r['retrieval_status'] == 'OK'
            prompt = r['calls'][0]['request']['messages'][0]['content'][0]['text']
            assert json.dumps(r['question'], ensure_ascii=False) in prompt
            assert json.dumps([f'{role}: {text}' for role, text in r['history']][-8:], ensure_ascii=False) in prompt
            # Check actual model-facing source rows, not merely saved ToolMessage.
            sent = json.loads(prompt.split('\n资料：', 1)[1])
            assert sent == [{'evidence_id': f'E{i}', 'text': e['text'],
                             'applicability': e['source'].get('applicability', {})}
                            for i, e in enumerate(evidence, 1)]
            checked_prompts += 1
            ids = {e['evidence_id'] for e in evidence}
            for claim in a['claims']:
                assert claim['text'] in a['answer'] and claim['citations']
                for cid in claim['citations']:
                    total_citations += 1
                    valid_citations += cid in ids
            assert set(a['citations']) <= ids
            full = (all(judgment['required_units_fully_covered']) and judgment['scenario_preserved']
                    and not judgment['unsupported_business_addition']
                    and not judgment['false_insufficiency'] and not judgment['ambiguous_scope_expression'])
            if a['error']:
                outcome = 'generation_error'
            elif a['abstained']:
                outcome = 'false_abstention' if judgment['false_insufficiency'] else 'appropriate_abstention'
            elif full:
                assert all(visible), 'full supported success requires actual evidence'
                outcome = 'full_supported_correct'
            else:
                outcome = 'incomplete_or_unsupported'
        outcomes[outcome] += 1
        categories.setdefault(item['category'], Counter())[outcome] += 1
        for call in r['calls']:
            assert call['request']['model'] == 'deepseek-v4-flash'
            calls.append(call)
        compact.append({'id':r['id'], 'category':item['category'], 'question':r['question'],
                        'history':r['history'], 'answer':a, 'retrieval_status':r['retrieval_status'],
                        'visible_required_units':visible,
                        'evidence_aliases':{f'E{i}':e['evidence_id'] for i,e in enumerate(evidence,1)},
                        'outcome':outcome, 'review':judgment,
                        'api_calls':len(r['calls']), 'latency_ms':r['latency_ms']})
    assert valid_citations == total_citations
    completion = json.loads((root / 'completion.json').read_text())
    assert len(calls) == completion['api_calls']
    usage = Counter()
    for c in calls:
        usage.update({k:v for k,v in c['response']['usage'].items() if isinstance(v,(int,float))})
    assert dict(usage) == completion['usage']
    def ids_where(fn):
        return [r['id'] for r in compact if fn(r)]
    metrics = {
        'cases':80, 'rule_families':20, 'outcomes':dict(outcomes),
        'strict_full_supported_accuracy':outcomes['full_supported_correct']/80,
        'complete_visible_evidence':complete_evidence,
        'unsupported_business_addition_ids':ids_where(lambda r:r['review']['unsupported_business_addition']),
        'false_insufficiency_ids':ids_where(lambda r:r['review']['false_insufficiency']),
        'ambiguous_expression_ids':ids_where(lambda r:r['review']['ambiguous_scope_expression']),
        'appropriate_insufficiency_ids':ids_where(lambda r:r['review']['appropriate_insufficiency']),
        'failures_with_complete_evidence':ids_where(lambda r:all(r['visible_required_units']) and r['outcome']!='full_supported_correct'),
        'category_outcomes':{k:dict(v) for k,v in categories.items()},
        'citation_id_checks':{'valid':valid_citations,'total':total_citations,'not_semantic_accuracy':True},
        'api_calls':len(calls),'usage':dict(usage),
        'review_scope':'Assistant semantic review; synthetic consumed regression set; no independent human review or official benchmark; not Agent/Flow/publication E2E.'
    }
    (root/'scored-answers.json').write_text(json.dumps(compact,ensure_ascii=False,indent=2)+'\n')
    (root/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n')
    source_paths = [root/'answers.jsonl',root/'answers.jsonl.gz',root/'semantic-review.json',
                    Path('data/eval/ecommerce-complex-v2/heldout.gold.json'),
                    Path('data/eval/ecommerce-answer-reference-v1/heldout.references.json'),Path(__file__)]
    audit={'cases':80,'actual_model_prompts_checked':checked_prompts,'reference_not_in_generation_inputs':True,
           'model_source_rows_equal_frozen_wire':True,'call_count_and_usage_reconciled':True,
           'hashes':{str(p):digest(p) for p in source_paths}}
    (root/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    lines=['# 80题实际答案与逐例评分','', '评分为助手语义复核，不是独立人工审定。三条必要条款完整口径；允许等义改写。','']
    for r in compact:
        lines.extend([f"## {r['id']} · {r['outcome']}",'',r['question'],'',
                      (r['answer'] or {}).get('answer','上游检索失败，未生成。'),'',
                      f"复核：{r['review']['note']}",''])
    (root/'answers-review.zh-CN.md').write_text('\n'.join(lines))
    print(json.dumps(metrics,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
