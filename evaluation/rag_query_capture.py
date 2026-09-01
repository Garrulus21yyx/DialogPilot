"""Capture bounded LLM query transformations once for offline RAG replay."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from anthropic import AsyncAnthropic

from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_pipeline.contracts import RagCase
from evaluation.rag_pipeline.dataset import RagDataset
from mcp.query_transformer import QUERY_TRANSFORM_PROMPT_VERSION, QueryTransformer


CAPTURE_SCHEMA_VERSION = 1


def select_query_cases(
    dataset: RagDataset,
    *,
    split: str,
    max_cases: int,
) -> tuple[RagCase, ...]:
    """Select one challenging turn per dialogue, balanced across support domains."""
    per_group: dict[str, RagCase] = {}
    for case in dataset.select_cases(split):
        current = per_group.get(case.group_id)
        rank = (bool(case.history), len(case.history), case.case_id)
        current_rank = (
            (bool(current.history), len(current.history), current.case_id)
            if current else (False, -1, "")
        )
        if current is None or rank > current_rank:
            per_group[case.group_id] = case
    by_domain: dict[str, list[RagCase]] = defaultdict(list)
    for case in per_group.values():
        domain = next(
            (item for item in case.query_types if item not in {"customer_support", "multi_turn", "standalone"}),
            "unknown",
        )
        by_domain[domain].append(case)
    for values in by_domain.values():
        values.sort(key=lambda case: hashlib.sha256(case.case_id.encode()).hexdigest())
    selected: list[RagCase] = []
    domains = sorted(by_domain)
    while len(selected) < max_cases and any(by_domain.values()):
        for domain in domains:
            if by_domain[domain] and len(selected) < max_cases:
                selected.append(by_domain[domain].pop())
    return tuple(selected)


def capture_raw_queries(
    dataset: RagDataset,
    *,
    split: str,
    max_cases: int,
) -> dict[str, Any]:
    """Create a deterministic no-model capture for retrieval-only stress suites."""
    selected = select_query_cases(dataset, split=split, max_cases=max_cases)
    rows = [{
        "case_id": case.case_id,
        "group_id": case.group_id,
        "query_types": list(case.query_types),
        "history_turns": len(case.history),
        "raw_query": case.query,
        "standalone": "",
        "expansions": [],
        "hyde": "",
        "errors": [],
        "usage": {
            "calls": 0,
            "errors": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": {"sum": 0.0},
        },
        "usage_by_stage": {},
    } for case in selected]
    rows.sort(key=lambda row: row["case_id"])
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "dataset_id": dataset.manifest["dataset_id"],
        "split": split,
        "sample_policy": "one case per grounding group, deterministic domain round-robin",
        "case_count": len(rows),
        "prompt_version": "raw-only-v1",
        "model_policy": {"provider": "none", "rewrite": {"model": "none"}},
        "expansion_count": 0,
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "error_case_count": 0,
        "rows": rows,
    }


async def capture_queries(
    dataset: RagDataset,
    *,
    split: str,
    max_cases: int,
    concurrency: int,
    expansion_count: int,
) -> dict[str, Any]:
    policy = ModelPolicy.from_env()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if policy.base_url:
        client_kwargs["base_url"] = policy.base_url
    client = AsyncAnthropic(**client_kwargs)
    profile = policy.profile(ModelRole.REWRITE)
    transformer = QueryTransformer(client, profile)
    selected = select_query_cases(dataset, split=split, max_cases=max_cases)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(case: RagCase) -> dict[str, Any]:
        async with semaphore:
            with capture_llm_usage() as standalone_usage:
                standalone, standalone_error = await transformer.standalone(case.query, case.history)
            with capture_llm_usage() as expansion_usage:
                expansions, expansion_error = await transformer.expand(
                    standalone, n=expansion_count,
                )
            with capture_llm_usage() as hyde_usage:
                hyde, hyde_error = await transformer.hyde(standalone)
            stage_usage = {
                "standalone": standalone_usage.summary()["total"],
                "multi_query": expansion_usage.summary()["total"],
                "hyde": hyde_usage.summary()["total"],
            }
            usage = {
                field: sum(int(item[field]) for item in stage_usage.values())
                for field in ("calls", "errors", "input_tokens", "output_tokens")
            }
            usage["latency_ms"] = {
                "sum": sum(float(item["latency_ms"]["sum"]) for item in stage_usage.values()),
            }
            errors = [
                f"{stage}:{error}" for stage, error in (
                    ("standalone", standalone_error),
                    ("multi_query", expansion_error),
                    ("hyde", hyde_error),
                ) if error
            ]
            return {
                "case_id": case.case_id,
                "group_id": case.group_id,
                "query_types": list(case.query_types),
                "history_turns": len(case.history),
                "raw_query": case.query,
                "standalone": standalone,
                "expansions": list(expansions),
                "hyde": hyde,
                "errors": errors,
                "usage": usage,
                "usage_by_stage": stage_usage,
            }

    rows = await asyncio.gather(*(one(case) for case in selected))
    rows.sort(key=lambda row: row["case_id"])
    total_calls = sum(int(row["usage"]["calls"]) for row in rows)
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "dataset_id": dataset.manifest["dataset_id"],
        "split": split,
        "sample_policy": "one maximum-history turn per dialogue, domain round-robin",
        "case_count": len(rows),
        "prompt_version": QUERY_TRANSFORM_PROMPT_VERSION,
        "model_policy": {
            "provider": policy.provider,
            "base_url": policy.base_url or "official",
            "rewrite": profile.to_dict(),
        },
        "expansion_count": expansion_count,
        "total_calls": total_calls,
        "total_input_tokens": sum(int(row["usage"]["input_tokens"]) for row in rows),
        "total_output_tokens": sum(int(row["usage"]["output_tokens"]) for row in rows),
        "error_case_count": sum(bool(row["errors"]) for row in rows),
        "rows": rows,
    }


def write_capture(path: Path, capture: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(capture, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--split", choices=("dev", "heldout", "stress"), default="dev")
    parser.add_argument("--max-cases", type=int, default=48)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--expansion-count", type=int, default=2)
    parser.add_argument(
        "--raw-only", action="store_true",
        help="Capture deterministic Raw queries without an API key or model calls",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.max_cases < 1:
        parser.error("--max-cases must be positive")
    dataset = RagDataset.load(args.dataset)
    capture = (
        capture_raw_queries(dataset, split=args.split, max_cases=args.max_cases)
        if args.raw_only else
        asyncio.run(capture_queries(
            dataset,
            split=args.split,
            max_cases=args.max_cases,
            concurrency=args.concurrency,
            expansion_count=args.expansion_count,
        ))
    )
    write_capture(args.output, capture)
    print(json.dumps({
        "case_count": capture["case_count"],
        "total_calls": capture["total_calls"],
        "total_input_tokens": capture["total_input_tokens"],
        "total_output_tokens": capture["total_output_tokens"],
        "error_case_count": capture["error_case_count"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
