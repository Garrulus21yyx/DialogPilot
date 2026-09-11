#!/usr/bin/env python3
"""Operate the evidence-first tau3 evaluation and RCA loop."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.tau3_causal_probes import probe_report  # noqa: E402
from evaluation.tau3_gate import compare_and_gate  # noqa: E402
from evaluation.tau3_langfuse_scores import publish_scores  # noqa: E402
from evaluation.tau3_langfuse_evidence import enrich_from_langfuse  # noqa: E402
from evaluation.tau3_llm_judge import judge_report, structured_judge  # noqa: E402
from evaluation.tau3_rca import analyze_run  # noqa: E402
from evaluation.tau3_regression import generate_candidates, promote_candidates  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze artifacts and run causal probes")
    analyze.add_argument("run_dir", type=Path)
    analyze.add_argument("--output", type=Path)

    candidates = subparsers.add_parser("candidates", help="Generate reviewable regression candidates")
    candidates.add_argument("report", type=Path)
    candidates.add_argument("--output", type=Path)

    enrich = subparsers.add_parser("enrich-langfuse", help="Fetch session evidence and causal metadata")
    enrich.add_argument("report", type=Path)
    enrich.add_argument("--env-file", type=Path, default=ROOT / ".env")
    enrich.add_argument("--output", type=Path)

    inspect = subparsers.add_parser(
        "inspect", help="Build one local RCA, execution-chain, error, and token report",
    )
    inspect.add_argument("run_dir", type=Path)
    inspect.add_argument("--env-file", type=Path, default=ROOT / ".env")
    inspect.add_argument("--output", type=Path)

    judge = subparsers.add_parser("judge", help="Judge semantic hypotheses from bounded evidence")
    judge.add_argument("report", type=Path)
    judge.add_argument("--env-file", type=Path, default=ROOT / ".env")
    judge.add_argument("--output", type=Path)

    promote = subparsers.add_parser("promote", help="Promote explicitly reviewed candidates")
    promote.add_argument("candidates", type=Path)
    promote.add_argument("reviews", type=Path)
    promote.add_argument("--output", type=Path)

    gate = subparsers.add_parser("gate", help="Compare runs and evaluate active regressions")
    gate.add_argument("baseline", type=Path)
    gate.add_argument("candidate", type=Path)
    gate.add_argument("--regressions", type=Path)
    gate.add_argument("--policy", type=Path)
    gate.add_argument("--output", type=Path)

    publish = subparsers.add_parser("publish", help="Publish compact RCA scores to Langfuse")
    publish.add_argument("report", type=Path)
    publish.add_argument("--output", type=Path)

    args = parser.parse_args()
    exit_code = 0
    if args.command == "analyze":
        result = analyze_run(args.run_dir)
        result = probe_report(result, args.run_dir)
    elif args.command == "inspect":
        _load_env(args.env_file)
        result = probe_report(analyze_run(args.run_dir), args.run_dir)
        try:
            result = enrich_from_langfuse(result)
            result = probe_report(result, args.run_dir)
            result["observability_status"] = "AVAILABLE"
        except Exception as exc:
            result["observability_status"] = "UNAVAILABLE"
            result["observability_error"] = {"error_type": type(exc).__name__}
    elif args.command == "candidates":
        result = generate_candidates(_read(args.report))
    elif args.command == "enrich-langfuse":
        _load_env(args.env_file)
        result = enrich_from_langfuse(_read(args.report))
        result = probe_report(result, Path(result["run"]["path"]))
    elif args.command == "judge":
        from core.framework_models import framework_model
        from core.model_policy import ModelPolicy, ModelRole
        from infrastructure.langfuse_trace_sink import LangfuseTraceSink
        values = _load_env(args.env_file)
        policy = ModelPolicy.from_env(values)
        model = framework_model(
            policy.profile(ModelRole.VERIFIER),
            {"api_key": values["ANTHROPIC_API_KEY"], "base_url": policy.base_url},
            max_tokens=2048,
        )
        langfuse = LangfuseTraceSink.from_env()
        try:
            callbacks = (langfuse.callback(),) if langfuse else ()
            result = asyncio.run(judge_report(
                _read(args.report), structured_judge(model, callbacks=callbacks),
            ))
        finally:
            if langfuse:
                langfuse.close()
    elif args.command == "promote":
        result = promote_candidates(_read(args.candidates), _read(args.reviews))
    elif args.command == "gate":
        suite = _read(args.regressions) if args.regressions else None
        policy = _read(args.policy) if args.policy else None
        result = compare_and_gate(_read(args.baseline), _read(args.candidate), suite, policy)
        exit_code = int(result["exit_code"])
    else:
        result = publish_scores(_read(args.report))
    _emit(result, args.output)
    raise SystemExit(exit_code)


def _read(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text())


def _load_env(path: Path) -> Mapping[str, Any]:
    from dotenv import dotenv_values
    values = {**dotenv_values(path), **os.environ}
    for key, value in values.items():
        if key.startswith("LANGFUSE_") and value is not None:
            os.environ.setdefault(key, str(value))
    return values


def _emit(value: Mapping[str, Any], output: Path | None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
