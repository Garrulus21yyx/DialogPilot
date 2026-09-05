"""HTTP turn submission shared by interactive and acceptance clients."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


async def submit_turn(
    client: httpx.AsyncClient,
    message: str,
    *,
    conversation_id: str,
    request_id: str,
    asset_ids: tuple[str, ...] = (),
    wait_seconds: float = 60,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    """Submit once; observation timeout neither cancels nor repeats the turn."""
    payload = {
        "message": message, "conv_id": conversation_id, "request_id": request_id,
        "asset_ids": list(asset_ids),
    }
    response = await client.post("/chat", json=payload)
    response.raise_for_status()
    accepted = response.json()
    if accepted.get("outcome") != "accepted":
        return accepted
    invocation_key = accepted["public_status"]["invocation_key"]
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        response = await client.get(f"/invocations/{invocation_key}")
        response.raise_for_status()
        status = response.json()
        if status.get("final_response") or status.get("pending_signal"):
            return status
        runtime_status = (status.get("runtime") or {}).get("execution_status")
        if runtime_status not in {None, "QUEUED", "RUNNING"}:
            return status
        await asyncio.sleep(poll_seconds)
    return accepted
