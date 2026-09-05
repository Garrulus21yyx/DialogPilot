"""Run one reproducible authenticated local demo and write a sanitized JSON report."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
import uuid
from io import BytesIO

import httpx
import jwt
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from api.chat_client import submit_turn


def _demo_png() -> bytes:
    image = Image.new("RGB", (700, 180), "white")
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52,
        )
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text(
        (24, 52), "ERROR CODE E42", fill="black", font=font,
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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


async def run(base_url: str, message: str) -> dict[str, object]:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {_token()}"}
    request_id = f"local-e2e-{uuid.uuid4().hex}"
    conv_id = f"local-e2e-conversation-{uuid.uuid4().hex}"
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=120.0, headers=headers,
    ) as client:
        health_response = await client.get("/health")
        health_response.raise_for_status()
        health = health_response.json()

        knowledge_response = await client.get("/knowledge/stats")
        knowledge_response.raise_for_status()
        knowledge = knowledge_response.json()

        asset_response = await client.post(
            "/assets/upload",
            headers=headers,
            params={"conv_id": conv_id, "request_id": request_id},
            files={"file": ("screen.png", _demo_png(), "image/png")},
        )
        asset_response.raise_for_status()
        asset = asset_response.json()

        result = await submit_turn(
            client, message, conversation_id=conv_id, request_id=request_id,
            asset_ids=(asset["asset_id"],), wait_seconds=120,
        )
        chat = result.get("final_response") or result

    storage = knowledge.get("storage_backend") or {}
    facts = (chat.get("evaluation_trace") or {}).get("consumption", {}).get("facts", [])
    passed = (
        health.get("status") == "ok"
        and storage.get("engine") == "postgresql+pgvector+pg_fts"
        and int(knowledge.get("total_chunks") or 0) > 0
        and asset.get("status") == "SCANNED"
        and asset.get("ocr_invoked") is False
        and asset.get("vlm_invoked") is False
        and bool(chat.get("response"))
        and chat.get("request_id") == request_id
        and chat.get("verified") is True
        and "E42" in str(chat.get("response") or "")
        and any(
            fact.get("requirement_id") == "media.visible_text"
            and fact.get("source_kind") == "MEDIA_OBSERVED"
            and fact.get("producer_id") == "media_read"
            for fact in facts
        )
    )
    return {
        "schema_version": "dialogpilot-local-e2e-v2",
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
        "attachment": {
            "http_status": asset_response.status_code,
            "modality": asset.get("modality"),
            "media_type": asset.get("media_type"),
            "status": asset.get("status"),
            "ocr_invoked": asset.get("ocr_invoked"),
            "vlm_invoked": asset.get("vlm_invoked"),
        },
        "chat": {
            "result_source": "invocation_query" if "final_response" in result else "chat_response",
            "intent": chat.get("intent"),
            "primary_agent": chat.get("primary_agent"),
            "verification_status": chat.get("verification_status"),
            "verified": chat.get("verified"),
            "grounded": chat.get("grounded"),
            "knowledge_used": chat.get("knowledge_used"),
            "escalated": chat.get("escalated"),
            "latency_ms": chat.get("latency_ms"),
            "response_present": bool(chat.get("response")),
            "response_mentions_error_code": "E42" in str(chat.get("response") or ""),
            "consumed_evidence": facts,
        },
        "scope_limit": "Synthetic local request; no production traffic or legacy-data claim.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:18000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--message", default="请读取截图中的错误码，并告诉我下一步如何排查。",
    )
    args = parser.parse_args()
    load_dotenv(args.env_file)
    report = asyncio.run(run(args.base_url, args.message))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{report['verification']}: {args.output}")
    return 0 if report["verification"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
