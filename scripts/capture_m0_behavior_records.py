#!/usr/bin/env python3
"""Capture small real-chain M0 characterization records without HTTP transport."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from application.chat_application import ChatCommand, Completed
from core.llm_metrics import capture_llm_usage
from core.tracing import trace_scope
from dotenv import load_dotenv


def _route(response: dict) -> str:
    disposition = str(response.get("routing_disposition") or "execute")
    if disposition != "execute":
        return disposition
    audits = list(response.get("tool_audit") or [])
    business = any(
        str(item.get("tool_name") or "") != "knowledge_search" for item in audits
    )
    knowledge = bool(response.get("knowledge_used"))
    if business and knowledge:
        return "mixed"
    if business:
        return "business_tool"
    if len(response.get("agent_types") or []) > 1:
        return "multi_domain"
    if knowledge:
        return "knowledge_qa"
    return "direct_agent"


def _project_failure_type(result, response: dict) -> str:
    failed_stage = next(
        (
            stage.stage for stage in getattr(result, "stages", ())
            if stage.status.value in {"failed", "degraded"}
        ),
        "",
    )
    if failed_stage:
        return f"stage:{failed_stage}"
    tool_statuses = [
        str(item.get("status") or "")
        for item in response.get("tool_audit") or []
    ]
    non_success = next(
        (status for status in tool_statuses if status not in {"success", "cached"}),
        "",
    )
    if non_success:
        return f"tool:{non_success}"
    return "none" if isinstance(result, Completed) else type(result).__name__


async def capture(args) -> None:
    from api import main

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty JSON array")

    redis_url = str(args.redis_url or os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        raise ValueError("REDIS_URL or --redis-url is required")
    if args.redis_host:
        parsed = urlsplit(redis_url)
        credentials = ""
        if parsed.username is not None:
            credentials = quote(parsed.username, safe="")
            if parsed.password is not None:
                credentials += f":{quote(parsed.password, safe='')}"
            credentials += "@"
        elif os.getenv("REDIS_PASSWORD"):
            credentials = f":{quote(os.environ['REDIS_PASSWORD'], safe='')}@"
        port = f":{parsed.port}" if parsed.port else ""
        redis_url = urlunsplit((
            parsed.scheme,
            f"{credentials}{args.redis_host}{port}",
            f"/{args.redis_db}" if args.redis_db is not None else parsed.path,
            parsed.query,
            parsed.fragment,
        ))

    with tempfile.TemporaryDirectory(prefix="dialogpilot-m0-") as temporary:
        root = Path(temporary)
        bundle_copy = root / "agent-bundles.db"
        shutil.copy2(args.bundle_db, bundle_copy)
        os.environ.update({
            "REDIS_URL": redis_url,
            "AGENT_BUNDLE_DB_PATH": str(bundle_copy),
            "BADCASE_DB_PATH": str(root / "badcases.db"),
            "CUSTOMER_OPERATIONS_DB_PATH": str(root / "operations.db"),
            "REACT_RUN_DB_PATH": str(root / "react-runs.db"),
            "PROMETHEUS_PORT": "0",
            # Baseline characterizes the current code contract against an isolated,
            # freshly built index. It must never repair or reinterpret a deployed
            # legacy collection as part of evaluation.
        })
        records = []
        async with main.lifespan(main.app):
            Path(args.rag_index_manifest).write_text(
                json.dumps(
                    main._knowledge_store.index_manifest,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            for index, case in enumerate(cases):
                trace_id = f"m0-{index}-{uuid.uuid4().hex[:16]}"
                request_id = f"m0-{index}-{uuid.uuid4().hex[:16]}"
                started = time.perf_counter()
                with trace_scope(trace_id), capture_llm_usage() as usage:
                    result = await main._chat_application().handle(ChatCommand(
                        message=str(case["message"]),
                        tenant_id="m0-baseline",
                        user_id=f"m0-user-{index}",
                        conv_id=f"m0-conversation-{index}",
                        request_id=request_id,
                    ))
                latency_ms = (time.perf_counter() - started) * 1000
                response = dict(result.response) if isinstance(result, Completed) else {}
                delivery_status = response.get("delivery_status")
                records.append({
                    "case_id": str(case.get("id") or index),
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "route": _route(response) if response else "typed_failure",
                    "latency_ms": latency_ms,
                    "model_calls": len(usage.calls),
                    "tool_calls": len(response.get("tool_audit") or []),
                    "publication_status": (
                        str(getattr(delivery_status, "value", delivery_status) or "completed")
                        if isinstance(result, Completed) else type(result).__name__
                    ),
                    "failure_type": _project_failure_type(result, response),
                    "stages": [stage.to_dict() for stage in getattr(result, "stages", ())],
                })
        missing_stage_evidence = [
            row["case_id"] for row in records if not row["stages"]
        ]
        if missing_stage_evidence:
            raise RuntimeError(
                f"capture missing stage evidence: {missing_stage_evidence}"
            )
        Path(args.output).write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records),
            encoding="utf-8",
        )


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rag-index-manifest", required=True)
    parser.add_argument("--bundle-db", default="data/evolution/agent-bundles.db")
    parser.add_argument("--redis-url", default="")
    parser.add_argument("--redis-host", default="")
    parser.add_argument("--redis-db", type=int, default=15)
    args = parser.parse_args()
    asyncio.run(capture(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
