"""Verify that a real HTTP span survives in PostgreSQL and is queryable."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import uuid

from dotenv import load_dotenv
import httpx
import jwt


def _token() -> str:
    secret = os.environ.get("AUTH_JWT_SECRET", "")
    if len(secret.encode()) < 32:
        raise RuntimeError("AUTH_JWT_SECRET must contain at least 32 bytes")
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": "local-trace-demo", "scope": "admin",
        "iss": os.environ.get("AUTH_JWT_ISSUER", "dialogpilot"),
        "aud": os.environ.get("AUTH_JWT_AUDIENCE", "dialogpilot-api"),
        "iat": now, "exp": now + timedelta(minutes=10),
    }, secret, algorithm="HS256")


def run(base_url: str) -> dict[str, object]:
    trace_id = uuid.uuid4().hex
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:
        health = client.get("/health", headers={"X-Trace-Id": trace_id})
        health.raise_for_status()
        trace = client.get(
            f"/traces/{trace_id}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        trace.raise_for_status()
    payload = trace.json()
    spans = payload["spans"]
    root = next(item for item in spans if item["parent_span_id"] == "")
    passed = all((
        health.status_code == 200,
        payload["trace_id"] == trace_id,
        payload["count"] >= 1,
        root["name"] == "http.get",
        root["kind"] == "server",
        root["status"] == "ok",
        root["attributes"]["http.path"] == "/health",
    ))
    return {
        "schema_version": "dialogpilot-local-trace-e2e-v1",
        "verification": "PASS" if passed else "FAIL",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "persisted_span_count": payload["count"],
        "root": {
            "name": root["name"], "kind": root["kind"],
            "status": root["status"], "attributes": root["attributes"],
        },
        "langfuse_export": (
            "enabled" if os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
            else "not_configured"
        ),
        "scope_limit": "Sanitized local HTTP trace; no production telemetry claim.",
    }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument(
        "--output", type=Path,
        default=Path("evaluation/reports/local-trace-e2e-v1.json"),
    )
    args = parser.parse_args()
    report = run(args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verification"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
