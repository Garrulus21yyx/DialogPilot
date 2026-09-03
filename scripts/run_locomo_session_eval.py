#!/usr/bin/env python3
"""Run the pinned LoCoMo session-retrieval smoke without production Memory."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.command_primary_eval.locomo_session_adapter import (  # noqa: E402
    LocomoAdapterError,
    load_locomo_single_hop_slice,
)
from evaluation.command_primary_eval.locomo_session_eval import (  # noqa: E402
    TokenOverlapSessionRetriever,
    evaluate_locomo_session_slice,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate pinned LoCoMo conv-26 single-hop session retrieval; "
            "production ServiceEpisode semantics are not evaluated."
        ),
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, object]:
    dataset = load_locomo_single_hop_slice(args.dataset)
    return dict(
        evaluate_locomo_session_slice(
            dataset=dataset,
            retriever=TokenOverlapSessionRetriever(),
            output_dir=args.output,
            run_id=str(args.run_id or uuid.uuid4().hex),
        )
    )


def main(argv: list[str] | None = None) -> int:
    try:
        report = run(parse_args(argv))
    except (LocomoAdapterError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
