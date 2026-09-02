"""Read-only Agent tool over the authoritative Commitment owner."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from mcp.tool_manager import Tool


def commitment_tools(service) -> tuple[Tool, ...]:
    async def list_commitments(
        params: dict[str, Any], context: Optional[dict[str, Any]],
    ):
        user_id = str((context or {}).get("user_id") or "").strip()
        if not user_id:
            raise ValueError("trusted user_id is required")
        items = await asyncio.to_thread(
            service.list_for_user,
            user_id=user_id,
            active_only=bool(params.get("active_only", True)),
            limit=min(max(int(params.get("limit", 10)), 1), 20),
        )
        return [{
            "commitment_id": item.commitment_id,
            "kind": item.kind,
            "description": item.description,
            "due_at": item.due_at,
            "owner": item.owner,
            "status": item.status.value,
            "version": item.version,
            "source_receipt_ref": item.source_receipt_ref,
            "ticket_id": item.ticket_id,
        } for item in items]

    return (Tool(
        name="commitment_list",
        description="查询当前登录用户的明确服务承诺、截止时间和履约状态；不要传 user_id",
        handler=list_commitments,
        schema={
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
        },
        allowed_agents=("general", "technical", "billing", "account_security"),
        read_only=True,
        authority="commitment.current_state",
        manifest_version="tool-manifest-v1",
        output_schema_version="commitment-list-v1",
        preconditions=("authenticated_user",),
        idempotency="read_only",
        retry_policy="safe_read_retry",
        typed_outcomes=("OK", "UNAVAILABLE", "UNAUTHORIZED"),
        output_fields=(
            "commitment_id", "kind", "description", "due_at", "owner",
            "status", "version", "source_receipt_ref", "ticket_id",
        ),
    ),)
