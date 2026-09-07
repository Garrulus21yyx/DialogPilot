"""Audit saved evaluation claims against source spans and the scoring ledger."""
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_provider_free import projection

ROOT = Path('artifacts/eval/rag-g3-membership-stages-2026-09-07')


def test_stage_evidence_is_traceable_and_summary_matches_all_cases():
    rows = [json.loads(l) for l in gzip.decompress((ROOT/'cases.jsonl.gz').read_bytes()).splitlines()]
    report = json.loads((ROOT/'report.json').read_text())
    snapshot = json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    with TemporaryDirectory() as temp:
        for name, text in snapshot.items():
            Path(temp, name).write_text(text)
        ds = RagDataset.load(Path(temp), verify_checksum=True)
    cases = ds.select_cases('dev')
    _, chunks, _ = projection(ds.documents, cases, 512, 64, 'structure_aware')
    chunks = {c.chunk_id: c for c in chunks}
    old = Path('artifacts/eval/rag-local-selection-stages-2026-09-07')
    manifest = json.loads((old/'manifest.json').read_text())
    locations = [(i, cid) for i, order in enumerate(manifest['candidate_orders']) for cid in order]
    ce = dict(zip(locations, np.load(old/'crossencoder-scores.npz')['values']))
    added = json.loads(gzip.decompress((ROOT/'added-scores.json.gz').read_bytes()))
    ce.update({(r['case_index'], r['candidate_id']): r['score'] for r in added})
    assert len(rows) == len(cases) == report['cases'] == 300
    def covers(case, ids):
        return all(any(chunks[cid].document_id == e.document_id and chunks[cid].start_char <= e.start_char and chunks[cid].end_char >= e.end_char for cid in ids) for e in case.evidence)
    for i, (row, case) in enumerate(zip(rows, cases)):
        assert row['case_id'] == case.case_id
        for arm in ('baseline', 'balanced'):
            value = row['arms'][arm]
            order = value['reranked_ids']
            assert len(order) == len(set(order)) <= 20
            assert order == sorted(order, key=lambda cid: (-ce[i, cid], cid))
            assert set(value['packed_ids']) <= set(order[:5])
            assert value['tokens'] <= 2600
            for field, selected in [('candidate_complete', order), ('rerank_complete', order[:5]), ('packed_complete', value['packed_ids'])]:
                assert value[field] == covers(case, selected)
        if 'candidate_change' in row:
            for witness in row['candidate_change']['witnesses']:
                removed = set(row['arms']['baseline']['reranked_ids']) - set(row['arms']['balanced']['reranked_ids'])
                assert set(witness['removed_gold']) == removed.intersection(witness['covering_ids'])
    for stage in ('candidate_complete', 'rerank_complete', 'packed_complete'):
        for arm in ('baseline', 'balanced'):
            assert report['arms'][arm][stage] == sum(r['arms'][arm][stage] for r in rows)
        assert report['paired'][stage]['rescues'] == sum(not r['arms']['baseline'][stage] and r['arms']['balanced'][stage] for r in rows)
        assert report['paired'][stage]['harms'] == sum(r['arms']['baseline'][stage] and not r['arms']['balanced'][stage] for r in rows)
