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
    load_locomo_single_hop_slice,
)
from evaluation.command_primary_eval.locomo_session_contracts import (  # noqa: E402
    SessionCandidateRetriever,
)
from evaluation.command_primary_eval.locomo_session_eval import (  # noqa: E402
    evaluate_locomo_session_slice,
)
from evaluation.command_primary_eval.locomo_session_retrievers import (  # noqa: E402
    TokenOverlapSessionRetriever,
    build_bge_m3_session_retriever,
    build_rrf_session_retriever,
)
from infrastructure.bge_m3_embedding import (  # noqa: E402
    BGEM3EmbeddingConfig,
    BGEM3EmbeddingUnavailable,
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
    parser.add_argument(
        "--retriever",
        choices=("token_overlap", "bge_m3", "rrf"),
        default="token_overlap",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, object]:
    dataset = load_locomo_single_hop_slice(args.dataset)
    retriever = _build_retriever(args.retriever)
    return dict(
        evaluate_locomo_session_slice(
            dataset=dataset,
            retriever=retriever,
            output_dir=args.output,
            run_id=str(args.run_id or uuid.uuid4().hex),
        )
    )


def _build_retriever(name: str) -> SessionCandidateRetriever:
    if name == "token_overlap":
        return TokenOverlapSessionRetriever()
    config = BGEM3EmbeddingConfig.from_env()
    if name == "bge_m3":
        return build_bge_m3_session_retriever(config)
    if name == "rrf":
        return build_rrf_session_retriever(config)
    raise ValueError(f"unsupported retriever: {name}")


def main(argv: list[str] | None = None) -> int:
    try:
        report = run(parse_args(argv))
    except (BGEM3EmbeddingUnavailable, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
