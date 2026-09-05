"""Text client for the same authenticated, durable /chat service as the UI."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

import httpx


async def submit_turn(
    client: httpx.AsyncClient,
    message: str,
    *,
    conversation_id: str,
    request_id: str,
    wait_seconds: float = 60,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    """Submit once; polling and reconnecting never create another invocation."""
    response = await client.post("/chat", json={
        "message": message,
        "conv_id": conversation_id,
        "request_id": request_id,
    })
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
    # A client timeout is not a cancellation or evidence of execution failure.
    return accepted


def display_response(result: dict[str, Any]) -> str:
    final = result.get("final_response") or result
    if isinstance(final.get("response"), str):
        return final["response"]
    pending = result.get("pending_signal") or {}
    challenge = pending.get("challenge")
    if isinstance(challenge, str):
        return challenge
    return json.dumps(result, ensure_ascii=False, indent=2)


async def run_cli() -> None:
    token = os.getenv("DIALOGPILOT_AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("CLI requires DIALOGPILOT_AUTH_TOKEN for the running API")
    base_url = os.getenv("DIALOGPILOT_API_URL", "http://localhost:8000")
    conversation_id = str(uuid.uuid4())
    print(f"DialogPilot CLI — conversation {conversation_id}; 输入 quit 退出")
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as client:
        while True:
            try:
                message = await asyncio.to_thread(input, "你: ")
            except (EOFError, KeyboardInterrupt):
                return
            message = message.strip()
            if message.lower() in {"quit", "exit", "退出"}:
                return
            if not message:
                continue
            request_id = uuid.uuid4().hex
            print(f"request_id: {request_id}")
            result = await submit_turn(
                client, message, conversation_id=conversation_id, request_id=request_id,
            )
            print(f"DialogPilot: {display_response(result)}\n")
