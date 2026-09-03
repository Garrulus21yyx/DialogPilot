#!/usr/bin/env python3
"""Run the production Structured LLM boundary on the frozen SGD benchmark."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from anthropic import AsyncAnthropic  # noqa: E402

from core.llm_metrics import capture_llm_usage  # noqa: E402
from core.model_policy import ModelPolicy, ModelRole  # noqa: E402
from evaluation.public_sgd import (  # noqa: E402
    SgdBenchmarkDataset,
    run_predictions,
    score_predictions,
)
from evaluation.public_sgd.contracts import (  # noqa: E402
    SgdAdapterError,
    load_json,
    load_jsonl,
    sha256_file,
    write_json,
    write_jsonl,
)
from infrastructure.anthropic_command_completion import (  # noqa: E402
    AnthropicCommandCompletion,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--allow-heldout",
        action="store_true",
        help="required to consume the frozen test split",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.split == "test" and not args.allow_heldout:
            raise SgdAdapterError("test split requires --allow-heldout")
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise SgdAdapterError("ANTHROPIC_API_KEY is required")
        policy = ModelPolicy.from_env()
        profile = policy.profile(ModelRole.INTENT)
        client_kwargs = {"api_key": api_key}
        if policy.base_url:
            client_kwargs["base_url"] = policy.base_url
        completion = AnthropicCommandCompletion(
            AsyncAnthropic(**client_kwargs),
            profile,
            max_tokens=args.max_tokens,
        )
        dataset = SgdBenchmarkDataset.load(args.dataset, args.split)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        predictions_path = args.output_dir / "predictions.jsonl"
        run_manifest_path = args.output_dir / "run-manifest.json"
        identity = {
            "dataset_manifest_sha256": sha256_file(args.dataset / "manifest.json"),
            "split": args.split,
            "provider": policy.provider,
            "model": profile.model,
            "limit": args.limit,
            "max_tokens": args.max_tokens,
        }
        if args.resume and run_manifest_path.exists():
            previous = load_json(run_manifest_path)
            if previous.get("identity") != identity:
                raise SgdAdapterError("resume configuration differs from prior run")
        elif args.resume:
            raise SgdAdapterError("resume requires an existing run manifest")
        elif run_manifest_path.exists():
            raise SgdAdapterError("run output exists; use --resume explicitly")
        write_json(run_manifest_path, {
            "schema_version": "dialogpilot-sgd-structured-run-manifest-v1",
            "identity": identity,
            "status": "RUNNING",
        })

        with capture_llm_usage() as usage:
            run = asyncio.run(run_predictions(
                dataset,
                completion,
                predictions_path,
                limit=args.limit,
                concurrency=args.concurrency,
                resume=args.resume,
            ))
        report = score_predictions(
            args.dataset / args.split / "cases.jsonl",
            predictions_path,
        ) if args.limit is None else _score_selected(
            dataset,
            predictions_path,
            args.output_dir,
        )
        write_json(args.output_dir / "report.json", report)
        run_manifest = {
            "schema_version": "dialogpilot-sgd-structured-run-manifest-v1",
            "identity": identity,
            "status": "COMPLETED",
            "run": run,
            "usage": usage.summary(),
        }
        write_json(run_manifest_path, run_manifest)
    except (OSError, ValueError, SgdAdapterError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({
        "run": run,
        "dimensions": report["dimensions"],
        "macro_mapping_rule_exact": report["macro_mapping_rule_exact"],
        "output_dir": str(args.output_dir),
    }, indent=2, sort_keys=True))
    return 0


def _score_selected(dataset, predictions_path: Path, output_dir: Path):
    prediction_ids = {
        str(row["case_id"])
        for row in load_jsonl(predictions_path)
    }
    selected_path = output_dir / "selected-cases.jsonl"
    write_jsonl(
        selected_path,
        (case for case in dataset.cases if str(case["case_id"]) in prediction_ids),
    )
    return score_predictions(selected_path, predictions_path)


if __name__ == "__main__":
    raise SystemExit(main())
