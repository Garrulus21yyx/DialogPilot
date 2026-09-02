"""Run one reproducible authenticated local demo and write a sanitized JSON report."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
import uuid

import httpx
import jwt
from dotenv import load_dotenv


def _token() -> str:
    secret = os.environ.get("AUTH_JWT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("AUTH_JWT_SECRET must contain at least 32 bytes")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "local-demo-user",
            "scope": "chat admin",
            "iss": os.environ.get("AUTH_JWT_ISSUER", "dialogpilot"),
            "aud": os.environ.get("AUTH_JWT_AUDIENCE", "dialogpilot-api"),
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        secret,
        algorithm="HS256",
    )


def run(base_url: str, message: str) -> dict[str, object]:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {_token()}"}
    request_id = f"local-e2e-{uuid.uuid4().hex}"
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=120.0) as client:
        health_response = client.get("/health")
        health_response.raise_for_status()
        health = health_response.json()

        knowledge_response = client.get("/knowledge/stats", headers=headers)
        knowledge_response.raise_for_status()
        knowledge = knowledge_response.json()

        chat_response = client.post(
            "/chat",
            headers=headers,
            json={"message": message, "request_id": request_id},
        )
        chat_response.raise_for_status()
        chat = chat_response.json()

    storage = knowledge.get("storage_backend") or {}
    passed = (
        health.get("status") == "ok"
        and storage.get("engine") == "postgresql+pgvector+pg_fts"
        and int(knowledge.get("total_chunks") or 0) > 0
        and bool(chat.get("response"))
        and chat.get("request_id") == request_id
    )
    return {
        "schema_version": "dialogpilot-local-e2e-v1",
        "environment": "docker-compose-local",
        "verification": "PASS" if passed else "FAIL",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "observed_duration_seconds": round(time.monotonic() - started, 3),
        "health": {
            "http_status": health_response.status_code,
            "status": health.get("status"),
            "storage": health.get("storage"),
        },
        "knowledge": {
            "http_status": knowledge_response.status_code,
            "storage_backend": knowledge.get("storage_backend"),
            "total_chunks": knowledge.get("total_chunks"),
        },
        "chat": {
            "http_status": chat_response.status_code,
            "intent": chat.get("intent"),
            "primary_agent": chat.get("primary_agent"),
            "verification_status": chat.get("verification_status"),
            "verified": chat.get("verified"),
            "grounded": chat.get("grounded"),
            "knowledge_used": chat.get("knowledge_used"),
            "escalated": chat.get("escalated"),
            "latency_ms": chat.get("latency_ms"),
            "response_present": bool(chat.get("response")),
        },
        "scope_limit": "Synthetic local request; no production traffic or legacy-data claim.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:18000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--message", default="退款需要满足什么条件？")
    args = parser.parse_args()
    load_dotenv(args.env_file)
    report = run(args.base_url, args.message)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{report['verification']}: {args.output}")
    return 0 if report["verification"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
