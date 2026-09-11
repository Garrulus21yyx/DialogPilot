from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.rag_production_heldout_artifacts import (
    write_combined_production_heldout_artifacts,
)


def test_combined_projection_recomputes_weighted_metrics_and_binds_components(tmp_path):
    first = _component(tmp_path / "first", "run-1", "case-1", "group-1", 1.0)
    second = _component(tmp_path / "second", "run-2", "case-2", "group-2", 0.0)

    report = write_combined_production_heldout_artifacts(
        output_dir=tmp_path / "combined",
        component_dirs=(first, second),
        run_id="combined-run",
    )

    assert report["case_count"] == 2
    assert report["candidate"]["all_evidence_recall"] == {
        "passed": 1, "total": 2, "rate": 0.5,
    }
    assert report["llm_usage"]["calls"] == 6
    manifest = json.loads((tmp_path / "combined" / "manifest.json").read_text())
    assert manifest["case_count"] == 2
    assert len(manifest["components"]) == 2


def test_combined_projection_rejects_modified_component(tmp_path):
    first = _component(tmp_path / "first", "run-1", "case-1", "group-1", 1.0)
    second = _component(tmp_path / "second", "run-2", "case-2", "group-2", 0.0)
    with (second / "predictions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{}\n")

    with pytest.raises(ValueError, match="checksum mismatch"):
        write_combined_production_heldout_artifacts(
            output_dir=tmp_path / "combined",
            component_dirs=(first, second),
            run_id="combined-run",
        )


def _component(
    path: Path, run_id: str, case_id: str, group_id: str, evidence_recall: float,
) -> Path:
    path.mkdir()
    metric = {
        "evidence_recall": evidence_recall,
        "document_recall": evidence_recall,
        "mrr": evidence_recall,
        "ndcg": evidence_recall,
    }
    row = {
        "run_id": run_id,
        "case_id": case_id,
        "group_id": group_id,
        "domain": "dmv",
        "document_length": "short_document",
        "retrieval_status": "OK",
        "candidate_metrics": metric,
        "prepack_metrics": metric,
        "packed_metrics": metric,
        "packing_change": "NEUTRAL",
        "rerank_fallback": False,
        "token_count": 10,
        "end_to_end_latency_ms": 1.0,
        "llm_usage": {"total": {
            "calls": 3, "errors": 0, "input_tokens": 10, "output_tokens": 2,
        }},
    }
    predictions_path = path / "predictions.jsonl"
    predictions_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = {
        "run_id": run_id,
        "run_status": "COMPLETED",
        "system_failure_count": 0,
        "case_count": 1,
    }
    report_path = path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "dataset": {
            "dataset_id": f"dataset-{run_id}",
            "case_count": 1,
            "cases_sha256": _sha(case_id.encode()),
            "corpus_sha256": "a" * 64,
        },
        "policy": {"fingerprint": "b" * 64},
        "embedding_profile": {"fingerprint": "c" * 64},
        "generation": {"immutable_fingerprint": "d" * 64},
        "model_policy": {"provider": "fixture"},
        "stages_executed": ["fixture"],
        "stages_not_run": [],
        "artifacts": {
            "predictions.jsonl": _sha(predictions_path.read_bytes()),
            "report.json": _sha(report_path.read_bytes()),
        },
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
