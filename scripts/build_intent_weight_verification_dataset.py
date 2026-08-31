#!/usr/bin/env python3
"""Build a fresh report-only upstream test subset after policy freeze."""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.dataset import DatasetBundle, write_dataset
from scripts.build_eval_dataset import build_banking, build_clinc_oos
from scripts.build_intent_weight_calibration_dataset import (
    EXCLUDED_BANKING_LABELS,
    _copy_for_suite,
    _stable,
)


DATASET_ID = "intent-weight-verification-2026-08-31"
TARGETS = {
    "account": 25,
    "account_security": 25,
    "logistics": 25,
    "payment_issue": 25,
    "query": 25,
    "refund": 25,
    "technical": 25,
    "technical_login": 25,
    "other": 100,
}


def _identity(source: Mapping[str, Any], message: str) -> tuple[str, str, str, str]:
    return (
        str(source.get("dataset") or ""),
        str(source.get("original_split") or ""),
        str(source.get("original_label") or ""),
        str(message),
    )


def build(output: Path, consumed_bundle_path: Path) -> DatasetBundle:
    consumed = DatasetBundle.load(consumed_bundle_path)
    consumed_test = {
        _identity(case.source, str(case.input["message"]))
        for case in consumed.select(layer="intent", split="heldout")
    }
    banking_rows, banking_source = build_banking(100)
    clinc_rows, clinc_source = build_clinc_oos(200)
    candidates = [
        row for row in banking_rows + clinc_rows
        if row["source"]["original_split"] in {"test", "oos_test"}
        and row["source"]["original_label"] not in EXCLUDED_BANKING_LABELS
        and _identity(row["source"], str(row["input"]["message"])) not in consumed_test
    ]
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        pools[str(row["expected"]["intent"])].append(row)
    selected = []
    for intent, count in TARGETS.items():
        rows = _stable(pools[intent], salt=f"{DATASET_ID}:{intent}")
        if len(rows) < count:
            raise RuntimeError(f"fresh heldout {intent} requires {count}, got {len(rows)}")
        for row in rows[:count]:
            copied = _copy_for_suite(row, split="heldout")
            copied["id"] = copied["id"].replace("calibration-heldout-", "verification-heldout-")
            copied["group_id"] = copied["id"]
            copied["tags"] = sorted(set(copied["tags"]) | {"fresh-upstream-test"})
            selected.append(copied)
    bundle = write_dataset(
        output,
        manifest={
            "dataset_id": DATASET_ID,
            "version": "1.0.0-auto-mapped",
            "status": "fresh_upstream_test_report_only",
            "created_at": date.today().isoformat(),
            "description": "Fresh official-test subset excluded from calibration and policy selection.",
            "split_policy": {"heldout": "unused upstream test/oos_test only"},
            "consumed_exclusion": {
                "dataset_id": consumed.manifest["dataset_id"],
                "cases_sha256": consumed.manifest["cases_sha256"],
                "excluded_heldout_count": len(consumed_test),
            },
            "mapping_policy": {
                "status": "auto_mapped_not_project_gold",
                "excluded_banking_labels": sorted(EXCLUDED_BANKING_LABELS),
                "targets": TARGETS,
            },
            "expected_distribution": {
                "by_layer": {"intent": sum(TARGETS.values())},
                "by_split": {"heldout": sum(TARGETS.values())},
                "by_layer_split": {"intent:heldout": sum(TARGETS.values())},
            },
            "sources": [banking_source, clinc_source],
        },
        cases=selected,
    )
    actual = Counter(case.expected["intent"] for case in bundle.cases)
    if actual != Counter(TARGETS):
        raise RuntimeError(f"fresh heldout distribution mismatch: {dict(actual)}")
    if any(
        _identity(case.source, str(case.input["message"])) in consumed_test
        for case in bundle.cases
    ):
        raise RuntimeError("fresh heldout overlaps the consumed calibration suite")
    return bundle


def main() -> int:
    bundle = build(
        ROOT / "data/eval" / DATASET_ID,
        ROOT / "data/eval/intent-weight-calibration-2026-08-31",
    )
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
