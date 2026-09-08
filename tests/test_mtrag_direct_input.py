import asyncio
import gzip
import json
from pathlib import Path
from scripts.replay_mtrag_direct_input import replay


def test_all_frozen_views_reach_compose_transport_intact():
    path=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
    rows=json.loads(gzip.decompress(path.read_bytes()))
    records=asyncio.run(replay(rows))
    assert len(records)==96
    assert all(r['full_view_equal'] and r['evidence_count']>0 for r in records)
    assert {(r['case_id'],r['arm']) for r in records} == {(r['case_id'],a) for r in rows for a in r['arms']}
