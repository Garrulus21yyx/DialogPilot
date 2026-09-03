#!/usr/bin/env python3
"""Replay the frozen Query run through identity selection and context packing."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.rag_pipeline.dataset import RagDataset  # noqa: E402
from evaluation.rag_selection_replay import (  # noqa: E402
    SELECTION_CONFIGS,
    replay_selection_grid,
)

SELECTED_QUERY_CONFIG = {
    "config_id": "raw-000-standalone-100",
    "raw_weight": 0.0,
    "standalone_weight": 1.0,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument(
        "--query-run",
        required=True,
        type=Path,
        help="Directory containing the frozen Query manifest/predictions/report",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, run_id: str | None = None) -> Mapping[str, Any]:
    dataset = RagDataset.load(args.dataset, verify_checksum=True)
    paths = {
        name: args.query_run / name
        for name in ("manifest.json", "predictions.jsonl", "report.json")
    }
    raw = {name: path.read_bytes() for name, path in paths.items()}
    query_manifest = json.loads(raw["manifest.json"])
    query_report = json.loads(raw["report.json"])
    query_rows = tuple(
        json.loads(line)
        for line in raw["predictions.jsonl"].decode("utf-8").splitlines()
        if line.strip()
    )
    digests = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
    _validate_query_run(dataset, query_manifest, query_rows, query_report, digests)
    identity = run_id or uuid.uuid4().hex
    predictions, report = replay_selection_grid(
        dataset=dataset,
        query_rows=query_rows,
        manifest_fingerprint=str(query_manifest["generation"]["manifest_fingerprint"]),
        run_id=identity,
    )
    manifest = _manifest(dataset, query_manifest, digests, identity, report)
    args.output.mkdir(parents=True, exist_ok=True)
    _write_json(args.output / "manifest.json", manifest)
    _write_jsonl(args.output / "predictions.jsonl", predictions)
    _write_json(args.output / "report.json", report)
    return report


def _validate_query_run(dataset, manifest, rows, report, digests) -> None:
    dataset_info = manifest.get("dataset") or {}
    expected_dataset = {
        "dataset_id": dataset.manifest.get("dataset_id"),
        "corpus_sha256": dataset.manifest.get("corpus_sha256"),
        "cases_sha256": dataset.manifest.get("cases_sha256"),
    }
    if any(dataset_info.get(key) != value for key, value in expected_dataset.items()):
        raise ValueError("Query run is not bound to the supplied dataset")
    if str(dataset_info.get("split")) != "dev":
        raise ValueError("selection replay supports the viewed Dev run only")
    if dict(report.get("selected_config") or {}) != SELECTED_QUERY_CONFIG:
        raise ValueError("Query run does not contain the frozen selected config")
    if SELECTED_QUERY_CONFIG not in (manifest.get("offline_replay") or {}).get(
        "configs", ()
    ):
        raise ValueError("selected Query config is absent from its manifest")
    predictions_sha = str(digests.get("predictions.jsonl") or "")
    if predictions_sha != str((manifest.get("source_capture") or {}).get("sha256")):
        raise ValueError("Query predictions checksum does not match its manifest")
    if predictions_sha != str((report.get("source_capture") or {}).get("sha256")):
        raise ValueError("Query predictions checksum does not match its report")
    expected_count = int((manifest.get("cohort") or {}).get("case_count", -1))
    if expected_count != len(rows) or int(report.get("case_count", -1)) != len(rows):
        raise ValueError("Query cohort size drift")
    case_ids = [str(row.get("case_id") or "") for row in rows]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate Query case ID")


def _manifest(dataset, query_manifest, digests, run_id, report):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "VIEWED_DEV_SELECTION_PACKING",
        "promotion_allowed": False,
        "dataset": {
            "dataset_id": dataset.manifest["dataset_id"],
            "split": "dev",
            "corpus_sha256": dataset.manifest["corpus_sha256"],
            "cases_sha256": dataset.manifest["cases_sha256"],
            "case_count": report["case_count"],
        },
        "upstream_query_run": {
            "run_id": query_manifest["run_id"],
            "input_sha256": dict(digests),
            "selected_config": SELECTED_QUERY_CONFIG,
            "chunk_profile": query_manifest["chunk_profile"],
            "embedding_profile": query_manifest["embedding_profile"],
            "generation": query_manifest["generation"],
        },
        "configs": [asdict(item) for item in SELECTION_CONFIGS],
        "stages_executed": [
            "frozen_candidate_replay",
            "identity_no_rerank",
            "context_packing",
            "evidence_pack_projection",
        ],
        "stages_not_run": [
            "postgres",
            "embedding_model",
            "live_query_rewrite",
            "model_rerank",
            "parent_expansion",
            "generation",
            "judge",
        ],
        "runtime_invocations": {
            "postgres": 0,
            "embedding_model": 0,
            "query_rewrite_model": 0,
            "rerank_model": 0,
            "generation_model": 0,
            "judge_model": 0,
        },
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    try:
        report = run(parse_args(argv))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "selected_config": report["selected_config"],
                "case_count": report["case_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
