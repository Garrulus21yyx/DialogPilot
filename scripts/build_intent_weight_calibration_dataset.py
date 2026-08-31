#!/usr/bin/env python3
"""Build a compact upstream-split intent calibration dataset.

Only parameter-selection data comes from upstream train. Upstream test remains
held out, and ambiguous source-to-project mappings are excluded explicitly.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.dataset import DatasetBundle, write_dataset
from scripts.build_eval_dataset import build_banking, build_clinc_oos


DATASET_ID = "intent-weight-calibration-2026-08-31"
TARGETS = {
    "account": 50,
    "account_security": 50,
    "logistics": 50,
    "payment_issue": 50,
    "query": 50,
    "refund": 50,
    "technical": 50,
    "technical_login": 50,
    "other": 100,
}

# These labels span identity-verification policy and login mechanics. Mapping
# them to one project label would manufacture ground truth for the exact
# boundary the evaluation is meant to measure.
EXCLUDED_BANKING_LABELS = frozenset({
    "unable_to_verify_identity",
    "verify_my_identity",
})


def _stable(rows: Iterable[Mapping[str, Any]], *, salt: str) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: hashlib.sha256(
            f"{salt}:".encode("utf-8")
            + json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )


def _copy_for_suite(row: Mapping[str, Any], *, split: str) -> dict[str, Any]:
    copied = json.loads(json.dumps(row, ensure_ascii=False))
    source_id = str(copied["id"])
    copied["id"] = f"calibration-{split}-{source_id.removeprefix('external-')}"
    copied["group_id"] = copied["id"]
    copied["split"] = split
    copied["source"]["calibration_split_owner"] = (
        "upstream_train" if split == "dev" else "upstream_test"
    )
    copied["tags"] = sorted(set(copied.get("tags") or []) | {"intent-weight-calibration"})
    return copied


def build(output: Path) -> DatasetBundle:
    banking_rows, banking_source = build_banking(max(TARGETS.values()))
    clinc_rows, clinc_source = build_clinc_oos(TARGETS["other"])
    allowed_banking = [
        row for row in banking_rows
        if row["source"]["original_label"] not in EXCLUDED_BANKING_LABELS
    ]
    pools: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in allowed_banking + clinc_rows:
        upstream_split = str(row["source"]["original_split"])
        split = "dev" if upstream_split in {"train", "oos_train"} else "heldout"
        pools[(split, str(row["expected"]["intent"]))].append(row)

    selected: list[dict[str, Any]] = []
    for split in ("dev", "heldout"):
        for intent, count in TARGETS.items():
            candidates = _stable(pools[(split, intent)], salt=f"{DATASET_ID}:{split}:{intent}")
            if len(candidates) < count:
                raise RuntimeError(
                    f"{split}:{intent} requires {count} unambiguous rows, got {len(candidates)}"
                )
            selected.extend(_copy_for_suite(row, split=split) for row in candidates[:count])

    expected_by_split = {"dev": sum(TARGETS.values()), "heldout": sum(TARGETS.values())}
    expected_matrix = {
        f"intent:{split}": count for split, count in expected_by_split.items()
    }
    bundle = write_dataset(
        output,
        manifest={
            "dataset_id": DATASET_ID,
            "version": "1.0.0-auto-mapped",
            "status": "auto_mapped_external_calibration",
            "created_at": date.today().isoformat(),
            "description": (
                "Compact external calibration suite. Parameters may be selected on dev only; "
                "heldout is upstream test and report-only."
            ),
            "split_policy": {
                "dev": "upstream train/oos_train",
                "heldout": "upstream test/oos_test",
            },
            "mapping_policy": {
                "status": "auto_mapped_not_project_gold",
                "excluded_banking_labels": sorted(EXCLUDED_BANKING_LABELS),
                "targets_per_split": TARGETS,
            },
            "expected_distribution": {
                "by_layer": {"intent": sum(expected_by_split.values())},
                "by_split": expected_by_split,
                "by_layer_split": expected_matrix,
            },
            "sources": [banking_source, clinc_source],
        },
        cases=selected,
    )
    actual = Counter((case.split, case.expected["intent"]) for case in bundle.cases)
    expected = Counter(
        (split, intent) for split in ("dev", "heldout")
        for intent, count in TARGETS.items() for _ in range(count)
    )
    if actual != expected:
        raise RuntimeError("written calibration distribution differs from the selection contract")
    return bundle


def main() -> int:
    output = ROOT / "data/eval" / DATASET_ID
    bundle = build(output)
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
