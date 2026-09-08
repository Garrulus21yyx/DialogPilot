"""Audit post-hoc, author-assessed reference points against frozen outputs.

Substring witnesses verify annotations, not semantic correctness. This is a
nine-point development diagnostic, not a benchmark or automatic answer judge.
"""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path('artifacts/eval/wixqa-real-entry-scoped-pair2-v2-2026-09-08')
# case, point, scope, source witness, answer witnesses (.25, .5)
POINTS = [
    (0, 'Published sites remain editable', 'core', 'edit your site at any time', ['edit your site at any time', 'edit your published site at any time']),
    (0, 'Open editor from site dashboard', 'core', "from your site's dashboard", ["from your site's dashboard", "site's dashboard"]),
    (0, 'Click Edit Site', 'reference_detail', 'Click Edit Site', [None, '**Edit Site**']),
    (0, 'Use the correct account', 'reference_detail', 'right account', [None, 'correct account']),
    (1, 'Monthly subscription covers campaigns', 'core', 'billed monthly', ['monthly subscription', 'monthly subscription']),
    (1, 'Extra one-time credits do not change subscription', 'core', 'won’t affect your overall monthly subscription', ['doesn’t affect your monthly subscription', "doesn't affect your monthly subscription"]),
    (1, 'Wix fee 15 percent, ad credit 85 percent', 'core', 'remaining 85%', ['remaining 85%', 'remaining 85%']),
    (1, 'Pausing last active campaign disables auto-renew', 'reference_detail', 'auto renew is turned off', [None, None]),
    (1, 'Manage subscription through campaign dashboard', 'reference_detail', 'manage your subscription from the campaigns page', [None, '**Manage Spend & Credits**']),
]

def main():
    arms = {}
    for arm_index, arm in enumerate(('0.25', '0.5')):
        rows = [json.loads(line) for line in gzip.decompress((ROOT / arm / 'full-cases.jsonl.gz').read_bytes()).decode().splitlines()]
        annotations = []
        for case, point, scope, source_phrase, witnesses in POINTS:
            row = rows[case]
            wire = json.loads(row['tools'][0]['result']['output_for_model'])
            evidence = [e['evidence_id'] for e in wire['evidence'] if source_phrase.lower() in e['text'].lower()]
            answer = row['outcome']['response']['response']
            witness = witnesses[arm_index]
            if witness:
                assert witness.lower() in answer.lower(), (arm, point)
                assert evidence, (arm, point, 'unsupported witness')
            annotations.append({'case_index': case, 'point': point, 'scope': scope,
                                'model_visible_evidence_ids': evidence,
                                'answer_covered_author_assessment': witness is not None,
                                'answer_quote_witness': witness,
                                'answer_sha256': hashlib.sha256(answer.encode()).hexdigest()})
        arms[arm] = {'annotations': annotations,
                     'core_covered': sum(x['answer_covered_author_assessment'] for x in annotations if x['scope'] == 'core'),
                     'core_total': 5,
                     'reference_detail_covered': sum(x['answer_covered_author_assessment'] for x in annotations if x['scope'] == 'reference_detail'),
                     'reference_detail_total': 4}
    result = {'scope': 'Post-hoc author annotation of two consumed dev examples; no independent review; no accuracy or causal improvement claim.',
              'source': 'WixQA ExpertWritten rows 166 and 112; optional additions beyond reference excluded from coverage denominator.',
              'new_api_calls': 0, 'arms': arms}
    (ROOT / 'reference-points.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print({arm: {k: v for k, v in value.items() if k != 'annotations'} for arm, value in arms.items()})

if __name__ == '__main__':
    main()
