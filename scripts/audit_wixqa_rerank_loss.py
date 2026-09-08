"""Replay recorded Wix rerank boundaries; no model or database calls."""
import gzip
import json
from pathlib import Path

ROOT = Path('artifacts/eval/wixqa-real-entry-scoped-pair2-v2-2026-09-08')

def main():
    arms = {}
    for arm in ('0.25', '0.5'):
        p = ROOT / arm
        row = json.loads(gzip.decompress((p / 'full-cases.jsonl.gz').read_bytes()).decode().splitlines()[1])
        routes = json.loads(gzip.decompress((p / 'source-route-captures.json.gz').read_bytes()))[1]
        call = row['api_calls'][1]
        prompt = call['request']['messages'][0]['content'][0]['text']
        candidates = json.loads(prompt.split('候选：', 1)[1])
        order = call['response']['content'][0]['input']['ordered_ids']
        assert len(order) == len(candidates) == len(set(order)) == 20
        assert set(order) == {c['id'] for c in candidates}
        mapping = {c['id']: source for c, source in zip(candidates, routes['fused_candidates'])}
        for c in candidates:
            assert c['text'] == mapping[c['id']]['content']
        pack = row['tools'][0]['result']['data']['evidence_pack']
        selected = [e['chunk_id'] for e in pack['items']]
        assert selected == [mapping[key]['chunk_id'] for key in order[:5]]
        gold = json.loads((p / 'selection.json').read_text())['cases'][1]['article_ids']
        locations = {g: [order.index(c['id']) + 1 for c in candidates if mapping[c['id']]['source_id'] == g] for g in gold}
        wire = json.loads(row['tools'][0]['result']['output_for_model'])
        pause = [e['evidence_id'] for e in wire['evidence'] if 'auto renew is turned off' in e['text']]
        assert pause
        arms[arm] = {'gold_article_rerank_positions': locations, 'pack_equals_first_five': True,
                     'skipped_budget': pack['skipped_budget'], 'skipped_redundant': pack['skipped_redundant'],
                     'visible_auto_renew_evidence_ids': pause}
    output = {'arms': arms, 'new_api_calls': 0,
              'interpretation': 'Article qrel omission is caused by rank beyond final_k; auto-renew condition remains model-visible. Answer completeness requires separate semantic assessment.'}
    (ROOT / 'rerank-loss-audit.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
