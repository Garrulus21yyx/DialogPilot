#!/usr/bin/env python3
"""Freeze a balanced Command IR benchmark from a complete adapted SGD pool."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.public_sgd import freeze_stratified_subset  # noqa: E402
from evaluation.public_sgd.contracts import SgdAdapterError  # noqa: E402


RULES = (
    "SGD_REQUIRED_SLOT_REQUEST",
    "SGD_SERVICE_CALL_FIRST",
    "SGD_SERVICE_CALL_REPEAT",
    "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--per-rule", type=int, default=300)
    parser.add_argument("--seed", default="dialogpilot-sgd-command-v1")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = freeze_stratified_subset(
            args.pool,
            args.output,
            per_rule={rule: args.per_rule for rule in RULES},
            seed=args.seed,
        )
    except (OSError, SgdAdapterError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({
        "dataset_id": manifest["dataset_id"],
        "publication_status": manifest["publication_status"],
        "splits": {
            split: value["case_count"] for split, value in manifest["splits"].items()
        },
        "output": str(args.output),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
