"""Exercise explicit commitment creation, automatic breach and late fulfillment."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
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
        "sub": "local-commitment-demo", "scope": "admin",
        "iss": os.environ.get("AUTH_JWT_ISSUER", "dialogpilot"),
        "aud": os.environ.get("AUTH_JWT_AUDIENCE", "dialogpilot-api"),
        "iat": now, "exp": now + timedelta(minutes=10),
    }, secret, algorithm="HS256")


def run(base_url: str) -> dict[str, object]:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {_token()}"}
    key = f"local-commitment-{uuid.uuid4().hex}"
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30) as client:
        created_response = client.post("/commitments", headers=headers, json={
            "idempotency_key": key,
            "user_id": "local-commitment-user",
            "conversation_id": f"conversation-{key}",
            "kind": "refund_followup",
            "description": "本地 Demo：确认退款最终状态",
            "due_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            "owner": "local-support-agent",
            "source_kind": "manual",
        })
        created_response.raise_for_status()
        item = created_response.json()["commitment"]
        commitment_id = item["commitment_id"]
        detail = item
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = client.get(
                f"/commitments/{commitment_id}", headers=headers,
            )
            response.raise_for_status()
            detail = response.json()
            if detail["status"] == "breached":
                break
            time.sleep(0.25)
        fulfilled_response = client.patch(
            f"/commitments/{commitment_id}/status", headers=headers,
            json={
                "status": "late_fulfilled",
                "expected_version": detail["version"],
                "actor": "refund-worker",
                "receipt_ref": f"refund-confirmation:{key}",
                "note": "authoritative refund status received after due time",
            },
        )
        fulfilled_response.raise_for_status()
        final_response = client.get(
            f"/commitments/{commitment_id}", headers=headers,
        )
        final_response.raise_for_status()
        final = final_response.json()
    event_states = [event["to_status"] for event in final["events"]]
    passed = all((
        created_response.json()["created"] is True,
        detail["status"] == "breached",
        final["status"] == "late_fulfilled",
        final["breached_at"] is not None,
        final["fulfilled_at"] is not None,
        event_states == ["scheduled", "breached", "late_fulfilled"],
    ))
    return {
        "schema_version": "dialogpilot-local-commitment-e2e-v1",
        "verification": "PASS" if passed else "FAIL",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "observed_duration_seconds": round(time.monotonic() - started, 3),
        "commitment_id": commitment_id,
        "final_status": final["status"],
        "version": final["version"],
        "event_states": event_states,
        "breach_preserved_after_late_fulfillment": final["breached_at"] is not None,
        "scope_limit": "Synthetic local admin/API flow; no production SLA claim.",
    }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument(
        "--output", type=Path,
        default=Path("evaluation/reports/local-commitment-e2e-v1.json"),
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
