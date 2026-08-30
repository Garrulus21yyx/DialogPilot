#!/usr/bin/env python3
"""在同一版本化数据集上运行三档 DeepSeek 模型消融。

脚本不修改 .env；它逐档重建同一个 Compose app 容器，运行 8 条意图和
7 条 routing/Agent case，最后恢复调用前配置。输出不包含 API Key/JWT。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

import jwt
from dotenv import load_dotenv


PROFILES = {
    "flash_off": ("deepseek-v4-flash", "none", "0"),
    "flash_high": ("deepseek-v4-flash", "high", "1024"),
    "pro_high": ("deepseek-v4-pro", "high", "1024"),
}

ROLES = (
    "INTENT", "WORKER", "REACT", "SYNTHESIS", "VERIFIER",
    "JUDGE", "MEMORY", "REWRITE", "RERANK",
)

PRICING_USD_PER_MILLION = {
    # Source/effective date are emitted in the report. Off-peak is 50% of peak.
    "deepseek-v4-flash": {
        "peak": {"cache_hit_input": 0.014, "cache_miss_input": 0.44, "output": 1.32},
        "off_peak": {"cache_hit_input": 0.007, "cache_miss_input": 0.22, "output": 0.66},
    },
    "deepseek-v4-pro": {
        "peak": {"cache_hit_input": 0.044, "cache_miss_input": 1.32, "output": 3.96},
        "off_peak": {"cache_hit_input": 0.022, "cache_miss_input": 0.66, "output": 1.98},
    },
}


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def token_cost(calls: List[Dict[str, Any]], window: str) -> float:
    total = 0.0
    for call in calls:
        rates = PRICING_USD_PER_MILLION[call["model"]][window]
        total += (
            int(call.get("input_tokens", 0)) * rates["cache_miss_input"]
            + int(call.get("cache_creation_input_tokens", 0)) * rates["cache_miss_input"]
            + int(call.get("cache_read_input_tokens", 0)) * rates["cache_hit_input"]
            + int(call.get("output_tokens", 0)) * rates["output"]
        ) / 1_000_000
    return total


def compose(args: argparse.Namespace, env: Dict[str, str], *parts: str) -> None:
    command = ["docker", "compose"]
    for path in args.compose_file:
        command.extend(["-f", path])
    command.extend(parts)
    subprocess.run(command, cwd=args.project_root, env=env, check=True)


def wait_for_health(base_url: str, timeout_s: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # service is expected to refuse during recreate
            last_error = type(exc).__name__
        time.sleep(1)
    raise RuntimeError(f"service health timeout ({last_error})")


def admin_token(env: Dict[str, str]) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "model-ablation",
            "scope": "admin",
            "iat": now,
            "exp": now + 3600,
            "iss": env.get("AUTH_JWT_ISSUER", "dialogpilot"),
            "aud": env.get("AUTH_JWT_AUDIENCE", "dialogpilot-api"),
        },
        env["AUTH_JWT_SECRET"],
        algorithm="HS256",
    )


def run_slice(base_url: str, token: str, *, split: str, layer: str) -> Dict[str, Any]:
    body = json.dumps({
        "dataset_id": "dialogpilot-v1",
        "split": split,
        "layers": [layer],
        "include_non_gold": True,
    }).encode()
    request = urllib.request.Request(
        f"{base_url}/eval/run",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=360) as response:
        result = json.load(response)
    result["http_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def summarize(profile_id: str, runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    calls: List[Dict[str, Any]] = []
    case_latencies: List[float] = []
    intent_total = intent_correct = 0
    route_scores: List[float] = []
    task_scores: List[float] = []
    outcomes: List[Dict[str, Any]] = []

    for run in runs:
        calls.extend(run.get("metadata", {}).get("llm_usage", {}).get("calls", []))
        for result in run.get("results", []):
            if result["test_id"] == "intent_recognition":
                details = result.get("metadata", {}).get("cases", [])
                intent_total += len(details)
                intent_correct += sum(row.get("expected") == row.get("predicted") for row in details)
                case_latencies.extend(float(row.get("latency_ms", 0)) for row in details)
                continue
            scores = result.get("scores", {})
            if "route_exact_match" in scores:
                route_scores.append(float(scores["route_exact_match"]))
            if "task_exact_match" in scores:
                task_scores.append(float(scores["task_exact_match"]))
            metadata = result.get("metadata", {})
            case_latencies.append(float(metadata.get("latency_ms", 0)))
            outcomes.extend(metadata.get("agent_outcomes", []))

    tool_loops = [row for row in outcomes if row.get("react_status") not in {None, "disabled"}]
    completed_loops = [row for row in tool_loops if row.get("react_status") == "completed"]
    successful_tasks = [row for row in outcomes if row.get("status") == "success"]
    input_tokens = sum(int(call.get("input_tokens", 0)) for call in calls)
    output_tokens = sum(int(call.get("output_tokens", 0)) for call in calls)
    cache_read = sum(int(call.get("cache_read_input_tokens", 0)) for call in calls)
    cache_creation = sum(int(call.get("cache_creation_input_tokens", 0)) for call in calls)
    model_latencies = [float(call.get("latency_ms", 0)) for call in calls]

    return {
        "profile_id": profile_id,
        "model": PROFILES[profile_id][0],
        "reasoning": PROFILES[profile_id][1],
        "case_count": intent_total + len(route_scores),
        "intent": {
            "correct": intent_correct,
            "total": intent_total,
            "accuracy": round(intent_correct / intent_total, 6) if intent_total else None,
        },
        "routing": {
            "owner_exact": round(sum(route_scores) / len(route_scores), 6) if route_scores else None,
            "owner_cases": len(route_scores),
            "task_exact": round(sum(task_scores) / len(task_scores), 6) if task_scores else None,
            "task_cases": len(task_scores),
        },
        "execution": {
            "task_success_rate": round(len(successful_tasks) / len(outcomes), 6) if outcomes else None,
            "task_outcomes": len(outcomes),
            "react_tool_loop_completion_rate": (
                round(len(completed_loops) / len(tool_loops), 6) if tool_loops else None
            ),
            "react_tool_loops": len(tool_loops),
            "tool_calls": sum(len(row.get("tool_call_ids") or []) for row in tool_loops),
        },
        "case_latency_ms": {
            "p50": round(percentile(case_latencies, 0.50), 3),
            "p95": round(percentile(case_latencies, 0.95), 3),
            "max": round(max(case_latencies), 3) if case_latencies else 0.0,
        },
        "model_call_latency_ms": {
            "p50": round(percentile(model_latencies, 0.50), 3),
            "p95": round(percentile(model_latencies, 0.95), 3),
            "max": round(max(model_latencies), 3) if model_latencies else 0.0,
        },
        "usage": {
            "calls": len(calls),
            "errors": sum(call.get("error") is not None for call in calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
            "thinking_calls": sum(int(call.get("thinking_blocks", 0)) > 0 for call in calls),
            "reasoning_tokens": None,
            "reasoning_tokens_note": "DeepSeek Anthropic usage does not expose this separately.",
        },
        "estimated_cost_usd": {
            "off_peak": round(token_cost(calls, "off_peak"), 8),
            "peak": round(token_cost(calls, "peak"), 8),
        },
        "slices": [{
            "split": run.get("metadata", {}).get("split"),
            "layer": (run.get("metadata", {}).get("layers") or [None])[0],
            "passed": run.get("passed"),
            "total": run.get("total"),
            "http_elapsed_ms": run.get("http_elapsed_ms"),
        } for run in runs],
    }


def profile_env(base: Dict[str, str], profile_id: str) -> Dict[str, str]:
    model, reasoning, minimum = PROFILES[profile_id]
    configured = dict(base)
    for role in ROLES:
        configured[f"MODEL_{role}"] = model
        configured[f"MODEL_{role}_REASONING"] = reasoning
        configured[f"MODEL_{role}_MIN_COMPLETION_TOKENS"] = minimum
    return configured


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(pathlib.Path(__file__).resolve().parents[1]))
    parser.add_argument("--compose-file", action="append", default=[])
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def write_checkpoint(
    output: pathlib.Path,
    summaries: List[Dict[str, Any]],
    raw_runs: Dict[str, Any],
) -> None:
    """每档结束立即持久化，后续档失败时不丢已完成证据。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema_version": 1,
        "incomplete": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profiles": summaries,
        "raw_runs": raw_runs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.project_root = str(pathlib.Path(args.project_root).resolve())
    if not args.compose_file:
        args.compose_file = [str(pathlib.Path(args.project_root) / "docker-compose.yml")]
    load_dotenv(pathlib.Path(args.project_root) / ".env")
    original_env = dict(os.environ)
    if not original_env.get("AUTH_JWT_SECRET"):
        raise RuntimeError("AUTH_JWT_SECRET is required")

    summaries = []
    raw_runs: Dict[str, Any] = {}
    output = pathlib.Path(args.output)
    try:
        for profile_id in PROFILES:
            env = profile_env(original_env, profile_id)
            compose(args, env, "up", "-d", "--no-deps", "--force-recreate", "dialogpilot")
            wait_for_health(args.base_url)
            token = admin_token(env)
            runs = [
                run_slice(args.base_url, token, split=split, layer=layer)
                for layer in ("intent", "routing")
                for split in ("dev", "heldout")
            ]
            raw_runs[profile_id] = runs
            summaries.append(summarize(profile_id, runs))
            write_checkpoint(output, summaries, raw_runs)
            print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
    finally:
        compose(args, original_env, "up", "-d", "--no-deps", "--force-recreate", "dialogpilot")
        wait_for_health(args.base_url)

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "registry_id": "dialogpilot-v1",
            "review_scope": "provisional",
            "intent_cases_per_profile": 8,
            "routing_cases_per_profile": 7,
            "runs_per_profile": 1,
        },
        "profiles": summaries,
        "pricing": {
            "currency": "USD",
            "unit": "per 1M tokens",
            "effective_from": "2026-08-16T16:00:00Z",
            "source": "https://api-docs.deepseek.com/quick_start/pricing",
            "rates": PRICING_USD_PER_MILLION,
        },
        "limitations": [
            "All 15 cases are provisional seeds, not human-reviewed gold.",
            "Each profile was run once; P50/P95 describe this run, not confidence intervals.",
            "Reasoning tokens are not separately exposed by DeepSeek Anthropic usage.",
            "Costs are lower-bound estimates when an Agent timeout cancels an in-flight call before the provider returns usage.",
            "Tool Exact is unavailable because cases do not yet label expected tool names; the report uses ReAct tool-loop completion rate.",
            "Routing uses supplied gold intent to isolate Planner/Agent execution; intent accuracy is measured separately.",
        ],
        "raw_runs": raw_runs,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={output}")


if __name__ == "__main__":
    main()
