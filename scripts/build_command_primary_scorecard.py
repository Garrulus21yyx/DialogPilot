#!/usr/bin/env python3
"""Build a four-layer index over explicit evaluation run artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.command_primary_eval.direct_runner import write_json  # noqa: E402
from evaluation.command_primary_eval.scorecard import build_scorecard  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, object]:
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("catalog must be a JSON object")
    scorecard = build_scorecard(catalog, base_dir=args.catalog.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, scorecard)
    return scorecard


def main(argv: list[str] | None = None) -> int:
    try:
        scorecard = run(parse_args(argv))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(scorecard, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
