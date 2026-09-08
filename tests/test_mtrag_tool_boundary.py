import asyncio
import gzip
import json
from pathlib import Path
from scripts.replay_mtrag_tool_boundary import replay


def test_persistence_retains_all_frozen_views_and_recoverable_originals():
    source=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
    rows=json.loads(gzip.decompress(source.read_bytes()))
    results=asyncio.run(replay(rows))
    assert len(results)==96
    assert all(not r['blocked'] and r['original_recovered'] for r in results)
    assert all(not r['offloaded'] and r['visible_content_sha256']==r['original_sha256'] for r in results)
