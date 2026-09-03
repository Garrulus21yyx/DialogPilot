#!/usr/bin/env python3
"""Validate checksums, source references, and command state invariants."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.public_sgd import validate_frozen_benchmark  # noqa: E402
from evaluation.public_sgd.contracts import SgdAdapterError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = validate_frozen_benchmark(args.dataset)
    except (OSError, SgdAdapterError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
