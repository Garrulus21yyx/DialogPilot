"""Capture typed customer-support information requirements for offline replay."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping

from anthropic import AsyncAnthropic

from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_pipeline.dataset import RagDataset
from mcp.query_requirements import REQUIREMENT_PROMPT_VERSION, QueryRequirementPlanner

CAPTURE_SCHEMA_VERSION = 1


async def capture_requirements(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    *,
    concurrency: int,
) -> dict[str, Any]:
    if query_capture.get("dataset_id") != dataset.manifest.get("dataset_id"):
        raise ValueError("query capture and dataset IDs do not match")
    policy = ModelPolicy.from_env()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if policy.base_url:
        kwargs["base_url"] = policy.base_url
    planner = QueryRequirementPlanner(
        AsyncAnthropic(**kwargs), policy.profile(ModelRole.REWRITE),
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(row: Mapping[str, Any]) -> dict[str, Any]:
        async with semaphore:
            with capture_llm_usage() as usage:
                plan = await planner.plan(
                    str(row.get("raw_query") or ""),
                    str(row.get("standalone") or ""),
                )
        return {
            "case_id": str(row["case_id"]),
            "raw_query": plan.raw_query,
            "standalone_query": plan.standalone_query,
            "query_kind": plan.query_kind,
            "requirements": [
                {"requirement_id": item.requirement_id, "query": item.query}
                for item in plan.requirements
            ],
            "error": plan.error,
            "usage": usage.summary()["total"],
        }

    rows = list(await asyncio.gather(*(
        one(row) for row in query_capture.get("rows") or ()
    )))
    rows.sort(key=lambda row: row["case_id"])
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "dataset_id": dataset.manifest["dataset_id"],
        "source_query_capture_prompt_version": query_capture.get("prompt_version"),
        "prompt_version": REQUIREMENT_PROMPT_VERSION,
        "split": query_capture.get("split"),
        "case_count": len(rows),
        "model_policy": {
            "provider": policy.provider,
            "base_url": policy.base_url or "official",
            "rewrite": policy.profile(ModelRole.REWRITE).to_dict(),
        },
        "query_kind_counts": {
            kind: sum(row["query_kind"] == kind for row in rows)
            for kind in ("simple", "parallel_composite", "dependent")
        },
        "error_case_count": sum(bool(row["error"]) for row in rows),
        "total_calls": sum(int(row["usage"]["calls"]) for row in rows),
        "total_input_tokens": sum(int(row["usage"]["input_tokens"]) for row in rows),
        "total_output_tokens": sum(int(row["usage"]["output_tokens"]) for row in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--query-capture", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = RagDataset.load(args.dataset)
    query_capture = json.loads(args.query_capture.read_text(encoding="utf-8"))
    result = asyncio.run(capture_requirements(
        dataset, query_capture, concurrency=args.concurrency,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": result["case_count"],
        "query_kind_counts": result["query_kind_counts"],
        "error_case_count": result["error_case_count"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
