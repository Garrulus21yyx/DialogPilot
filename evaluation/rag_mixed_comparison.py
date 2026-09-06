"""Paired descriptive stage signals for identical mixed-case definitions.

Counts measure routing/evidence/gate signals, never semantic answer accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path

SIGNALS = ('expected_tool_coverage', 'expected_source_visible',
           'expected_source_cited', 'knowledge_support_checked')


def compare(before, after):
    if before['definitions_sha256'] != after['definitions_sha256']:
        raise ValueError('case definitions differ')
    groups = []
    for summary in (before, after):
        rows = summary['results']
        ids = [r['case_id'] for r in rows]
        if (not rows or len(ids) != len(set(ids)) or not summary['complete_capture']
                or summary['expected_cases'] != len(rows)
                or summary['captured_cases'] != len(rows)):
            raise ValueError('comparison requires complete unique case captures')
        if any(type(r.get(k)) is not bool for r in rows for k in SIGNALS):
            raise ValueError('stage signals must be explicit booleans')
        groups.append({r['case_id']: r for r in rows})
    old, new = groups
    if old.keys() != new.keys():
        raise ValueError('case identities differ')
    metrics = {}
    for key in SIGNALS:
        rescued = sorted(i for i in old if not old[i][key] and new[i][key])
        harmed = sorted(i for i in old if old[i][key] and not new[i][key])
        metrics[key] = {
            'before': sum(r[key] for r in old.values()),
            'after': sum(r[key] for r in new.values()),
            'rescued': rescued, 'harmed': harmed,
            'delta_pp': 100 * (len(rescued) - len(harmed)) / len(old),
        }
    return {
        'scope': 'descriptive paired stage signals; not answer accuracy or isolated causal attribution',
        'definitions_sha256': before['definitions_sha256'],
        'before_capture_sha256': before['capture_sha256'],
        'after_capture_sha256': after['capture_sha256'],
        'cases': len(old), 'metrics': metrics,
        'api_calls': {'before': before['api_calls'], 'after': after['api_calls']},
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    raw = [path.read_bytes() for path in (args.before, args.after)]
    result = compare(*(json.loads(b) for b in raw))
    result['summary_sha256'] = [hashlib.sha256(b).hexdigest() for b in raw]
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
