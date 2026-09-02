"""Run the optional DeepSeek Vision local demo and write a sanitized report."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import time
import uuid

from dotenv import load_dotenv
import httpx
import jwt
from PIL import Image, ImageDraw, ImageFont


def _token() -> str:
    secret = os.environ.get("AUTH_JWT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("AUTH_JWT_SECRET must contain at least 32 bytes")
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": "local-vlm-demo", "scope": "chat admin",
        "iss": os.environ.get("AUTH_JWT_ISSUER", "dialogpilot"),
        "aud": os.environ.get("AUTH_JWT_AUDIENCE", "dialogpilot-api"),
        "iat": now, "exp": now + timedelta(minutes=10),
    }, secret, algorithm="HS256")


def _demo_image() -> bytes:
    image = Image.new("RGB", (800, 500), "#f4f7fb")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32,
        )
    except OSError:
        font = ImageFont.load_default()
    draw.rounded_rectangle(
        (200, 170, 610, 300), radius=18, fill="#d9dee8",
        outline="#9aa4b2", width=3,
    )
    draw.text((325, 215), "CONTINUE", fill="#7b8491", font=font)
    draw.rectangle((170, 140, 640, 330), outline="red", width=12)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def run(base_url: str) -> dict[str, object]:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {_token()}"}
    request_id = f"local-vlm-{uuid.uuid4().hex}"
    conv_id = f"local-vlm-conversation-{uuid.uuid4().hex}"
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=180) as client:
        upload_response = client.post(
            "/assets/upload", headers=headers,
            params={"conv_id": conv_id, "request_id": request_id},
            files={"file": ("ui.png", _demo_image(), "image/png")},
        )
        upload_response.raise_for_status()
        asset = upload_response.json()
        chat_response = client.post(
            "/chat", headers=headers,
            json={
                "message": (
                    "我无法登录，界面提示错误。"
                    "红框里的按钮位置和状态是什么？"
                ),
                "conv_id": conv_id, "request_id": request_id,
                "asset_ids": [asset["asset_id"]],
            },
        )
        chat_response.raise_for_status()
        chat = chat_response.json()
    media = chat.get("media") or {}
    outcomes = media.get("outcomes") or []
    producers = media.get("producers") or []
    response = str(chat.get("response") or "")
    used_visual_observation = any(
        term in response.casefold()
        for term in ("按钮", "button", "红框", "continue")
    )
    passed = all((
        upload_response.status_code == 201,
        chat_response.status_code == 200,
        chat.get("verified") is True,
        media.get("ocr_invoked") is True,
        media.get("vlm_invoked") is True,
        bool(outcomes) and all(item.get("status") == "SUCCEEDED" for item in outcomes),
        any(item.get("model") == "deepseek-v4-flash-vision-exp" for item in producers),
        used_visual_observation,
    ))
    return {
        "schema_version": "dialogpilot-local-vlm-e2e-v1",
        "environment": "docker-compose-local-vlm-enabled",
        "verification": "PASS" if passed else "FAIL",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "observed_duration_seconds": round(time.monotonic() - started, 3),
        "attachment": {
            "http_status": upload_response.status_code,
            "status": asset.get("status"),
            "media_type": asset.get("media_type"),
        },
        "chat": {
            "http_status": chat_response.status_code,
            "intent": chat.get("intent"),
            "primary_agent": chat.get("primary_agent"),
            "verified": chat.get("verified"),
            "verification_status": chat.get("verification_status"),
            "media": media,
            "response_uses_visual_observation": used_visual_observation,
        },
        "scope_limit": (
            "Synthetic local image and request; DeepSeek experimental vision "
            "model enabled explicitly; no production-traffic claim."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:18000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(args.env_file)
    report = run(args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{report['verification']}: {args.output}")
    return 0 if report["verification"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
