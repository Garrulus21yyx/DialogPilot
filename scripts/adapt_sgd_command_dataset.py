#!/usr/bin/env python3
"""Freeze official SGD dev/test turns as DialogPilot Command IR cases."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.public_sgd import convert_sgd  # noqa: E402
from evaluation.public_sgd.adapter import UPSTREAM_URI  # noqa: E402
from evaluation.public_sgd.contracts import SgdAdapterError  # noqa: E402


PINNED_UPSTREAM_COMMIT = "e852981ae34990f4358979625854259302feaa78"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="existing official SGD checkout")
    source.add_argument(
        "--download",
        action="store_true",
        help="clone the pinned official revision into a temporary directory",
    )
    parser.add_argument("--source-commit", default=PINNED_UPSTREAM_COMMIT)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--splits", nargs="+", default=["dev", "test"], choices=["dev", "test"]
    )
    parser.add_argument(
        "--max-dialogues",
        type=int,
        help="development-only deterministic prefix; omitted means full splits",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace):
    if args.source is not None:
        return convert_sgd(
            args.source,
            args.output,
            source_commit=args.source_commit,
            splits=tuple(args.splits),
            max_dialogues=args.max_dialogues,
        )
    if shutil.which("git") is None:
        raise SgdAdapterError("git is required for --download")
    with tempfile.TemporaryDirectory(prefix="dialogpilot-sgd-") as temp:
        checkout = Path(temp) / "source"
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", UPSTREAM_URI, str(checkout)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "--detach", args.source_commit],
            check=True,
        )
        return convert_sgd(
            checkout,
            args.output,
            source_commit=args.source_commit,
            splits=tuple(args.splits),
            max_dialogues=args.max_dialogues,
        )


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
    except (OSError, SgdAdapterError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(
        {
            "dataset_id": result.manifest["dataset_id"],
            "publication_status": result.manifest["publication_status"],
            "output": str(result.output_dir),
            "splits": result.manifest["splits"],
        },
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
