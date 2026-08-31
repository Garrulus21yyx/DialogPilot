#!/usr/bin/env python3
"""Select on dev or evaluate a previously frozen intent fusion policy."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.intent_weight_calibration import evaluate_frozen_policy, select_calibrated_policy


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frozen-from", type=Path)
    args = parser.parse_args()
    cases = _jsonl(args.cases)
    outputs = {row["case_id"]: row for row in _jsonl(args.outputs)}
    if args.frozen_from:
        selection = json.loads(args.frozen_from.read_text(encoding="utf-8"))
        report = evaluate_frozen_policy(cases, outputs, selection["frozen_policy"])
    else:
        report = select_calibrated_policy(cases, outputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {report['status']} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
