import gzip
import json
from pathlib import Path

from scripts.audit_rag_dataset_exposure import inspect, query_key


def test_exposure_survives_nesting_and_inherited_exclusions():
    witness = {'group_id': 'doc2dial-current', 'query': '  A QUESTION? ',
               'document_id': 'wixqa-article1', 'case_id': 'task1',
               'excluded_group_ids': ['doc2dial-historical']}
    for depth in range(15):
        value = witness
        for _ in range(depth):
            value = {'wrapper': [value]}
        buckets = [set() for _ in range(4)]
        inspect(value, *buckets)
        assert buckets == [{'doc2dial-current', 'doc2dial-historical'},
                           {query_key('a question?')}, {'task1'}, {'article1'}]


def test_inventory_preserves_scope_and_recomputable_totals():
    root = Path('artifacts/eval/rag-g4-exposure-audit-v2-2026-09-07')
    with gzip.open(root/'inventory.json.gz', 'rt') as f:
        inventory = json.load(f)
    report = json.loads((root/'report.json').read_text())
    assert not inventory['freshness_attested']
    assert inventory['api_calls'] == 0
    assert not inventory['parse_errors']
    assert all(c['matches'] for c in inventory['source_checks'])
    assert len(inventory['files']) == report['file_count']
    assert len({f['path'] for f in inventory['files']}) == report['file_count']
    groups = inventory['mtrag']['groups']
    assert sum(g['identity_match'] for g in groups) == report['mtrag']['identity_matched_groups']
    for name, config in inventory['wixqa'].items():
        for kind in ('query', 'article'):
            assert sum(r[kind+'_match'] for r in config['matches']) == report['wixqa'][name][kind+'_matches']
    d = inventory['doc2dial']
    assert len(d['not_observed_ids']) + d['locally_present_test_groups'] == d['official_test_groups']
