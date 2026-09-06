import gzip
import json
from pathlib import Path
import shutil

import pytest

from evaluation.rag_tool_calibration_report import summarize

ROOT = Path(__file__).resolve().parents[1]/'artifacts/eval/rag-tool-dev-2026-09-06'


def test_recompute_versioned_compressed_capture(tmp_path):
    for name in ('cases.jsonl.gz', 'completion.json'):
        shutil.copy(ROOT/name, tmp_path/name)
    report = summarize(tmp_path)
    assert report['cases'] == report['complete_visible_evidence'] == 20
    assert report['known_fused_top5_complete_evidence_lower_bound'] == 20
    assert report['api_calls'] == 40
    assert report['answer_errors'] == report['rerank_fallbacks'] == 0


def test_report_rejects_claimed_coverage_after_visible_evidence_loss(tmp_path):
    rows = [json.loads(line) for line in gzip.decompress((ROOT/'cases.jsonl.gz').read_bytes()).decode().splitlines()]
    rows[0]['tool_message']['evidence'] = []
    (tmp_path/'cases.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    shutil.copy(ROOT/'completion.json', tmp_path/'completion.json')
    with pytest.raises(ValueError, match='visible evidence metric drift'):
        summarize(tmp_path)
