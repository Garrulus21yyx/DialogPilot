import asyncio
import gzip
import json
from pathlib import Path
from scripts.replay_mtrag_tool_boundary import replay


def test_archive_preserves_frozen_views_at_both_budget_boundaries():
    source=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
    rows=json.loads(gzip.decompress(source.read_bytes()))
    for budget in (2840,256):
        results=asyncio.run(replay(rows,budget))
        assert len(results)==96
        assert all(not r['blocked'] and r['original_recovered'] for r in results)
        assert all(r['offloaded'] == (r['visible_content_sha256']!=r['original_sha256']) for r in results)
        if budget==256:assert all(r['offloaded'] for r in results)
