"""Recheck saved source coverage and paired totals without model calls."""
import gzip
import json
from pathlib import Path

from evaluation.rag_pipeline.dataset import RagDataset


def main():
    root = Path('artifacts/eval')
    stage = root / 'rag-local-selection-stages-2026-09-07'
    ds = RagDataset.load(root / 'doc2dial-rag-mini-dev-v1', verify_checksum=True)
    cases = {c.case_id: c for c in ds.select_cases('dev')}
    docs = {d.document_id: d for d in ds.documents}
    outcomes = {}
    checked = 0
    for label in ('current-fusion', 'selected-fusion', 'local-crossencoder'):
        rows = [json.loads(l) for l in gzip.decompress((stage / (label + '-cases.jsonl.gz')).read_bytes()).splitlines()]
        assert len(rows) == len(cases) == len({r['case_id'] for r in rows})
        report = json.loads((stage / (label + '.json')).read_text())
        for row in rows:
            case = cases[row['case_id']]
            for mode in ('anchor', 'adjacent', 'parent'):
                packed = row[mode]
                assert packed['tokens'] <= 2600 and len(packed['selected']) <= 5
                for span in packed['selected']:
                    assert 0 <= span['start'] < span['end'] <= len(docs[span['document_id']].content)
                complete = all(any(s['document_id'] == e.document_id and s['start'] <= e.start_char and s['end'] >= e.end_char for s in packed['selected']) for e in case.evidence)
                assert complete == packed['complete']
                checked += 1
        for mode in ('anchor', 'adjacent', 'parent'):
            assert sum(r[mode]['complete'] for r in rows) == report['modes'][mode]['complete']
        outcomes[label] = {r['case_id']: r['anchor']['complete'] for r in rows}
    for pair in json.loads((stage / 'paired-changes.json').read_text()):
        a, b = outcomes[pair['before']], outcomes[pair['after']]
        assert pair['rescues'] == sum(not a[k] and b[k] for k in a)
        assert pair['harms'] == sum(a[k] and not b[k] for k in a)
    fusion = json.loads((stage / 'fusion.json').read_text())
    groups = {}
    for row in fusion['oof_cases']:
        group = cases[row['case_id']].group_id
        assert groups.setdefault(group, row['fold']) == row['fold']
    api = root / 'rag-composition20-selected-2026-09-07'
    rows = [json.loads(l) for l in gzip.decompress((api / 'cases.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows) == 20 and len({cases[r['case_id']].group_id for r in rows}) == 20
    for row in rows:
        for arm in row['arms'].values():
            for evidence in arm['evidence']:
                s = evidence['source']
                assert evidence['text'] == docs[s['source_id']].content[s['start_char']:s['end_char']]
    assert sum(len(a['calls']) for r in rows for a in r['arms'].values()) == 40
    result = {'source_budget_coverage_checks': checked, 'paired_totals': 'passed', 'group_fold_integrity': 'passed', 'composition_source_identity': 'passed', 'api_calls_for_validation': 0}
    (stage / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
