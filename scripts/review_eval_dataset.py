#!/usr/bin/env python3
"""显式确认人工审核，并由数据 Owner 原子语义地重写 checksum。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from evaluation.dataset import DatasetBundle, DatasetValidationError, sha256_file, write_dataset


def mark_human_reviewed(
    dataset_path: str | Path,
    *,
    case_ids: List[str],
    reviewer: str,
    notes: str,
    reviewed_at: str | None = None,
) -> DatasetBundle:
    """只改变审核元数据；expected 标签必须由 reviewer 在调用前核对。"""
    root = Path(dataset_path)
    # 允许 reviewer 先修正 case 的 input/expected；corpus 仍必须保持已签名内容。
    bundle = DatasetBundle.load(root, verify_checksum=False)
    corpus_path = root / "corpus.jsonl"
    if corpus_path.is_file() and bundle.manifest.get("corpus_sha256") != sha256_file(corpus_path):
        raise DatasetValidationError("corpus.jsonl changed outside the corpus build workflow")
    requested = {case_id.strip() for case_id in case_ids if case_id.strip()}
    if not requested:
        raise ValueError("at least one --case-id is required")
    reviewer = reviewer.strip()
    notes = notes.strip()
    if not reviewer or not notes:
        raise ValueError("reviewer and notes are required")

    rows: List[Dict[str, Any]] = [
        json.loads(line)
        for line in (root / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    existing = {str(row.get("id") or "") for row in rows}
    missing = sorted(requested - existing)
    if missing:
        raise ValueError(f"unknown case ids: {missing}")
    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    for row in rows:
        if str(row.get("id")) in requested:
            row["review"] = {
                "status": "human_reviewed",
                "reviewer": reviewer,
                "reviewed_at": timestamp,
                "notes": notes,
            }
    manifest = dict(bundle.manifest)
    manifest["status"] = (
        "human_reviewed"
        if all(row.get("review", {}).get("status") == "human_reviewed" for row in rows)
        else "partially_reviewed"
    )
    return write_dataset(root, manifest=manifest, cases=rows, corpus=bundle.corpus)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark explicitly reviewed eval cases and recompute dataset checksums"
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument(
        "--confirm-human-review",
        action="store_true",
        help="confirm expected labels and ambiguity were inspected by a human",
    )
    args = parser.parse_args()
    if not args.confirm_human_review:
        parser.error("--confirm-human-review is required; this command cannot infer gold labels")
    bundle = mark_human_reviewed(
        args.dataset,
        case_ids=args.case_id,
        reviewer=args.reviewer,
        notes=args.notes,
    )
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
