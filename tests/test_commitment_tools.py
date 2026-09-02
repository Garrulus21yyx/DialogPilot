import asyncio
from datetime import datetime, timezone

from mcp.commitment_tools import commitment_tools


def test_commitment_read_tool_uses_trusted_user_and_exposes_owner_facts(
    commitment_service,
):
    own, _ = commitment_service.create(
        idempotency_key="own", user_id="user-1", conversation_id="conv-1",
        kind="followup", description="确认处理结果",
        due_at=datetime(2030, 1, 1, tzinfo=timezone.utc), owner="support-1",
        source_kind="manual",
    )
    commitment_service.create(
        idempotency_key="other", user_id="user-2", conversation_id="conv-2",
        kind="followup", description="other",
        due_at=datetime(2030, 1, 1, tzinfo=timezone.utc), owner="support-2",
        source_kind="manual",
    )
    [tool] = commitment_tools(commitment_service)

    result = asyncio.run(tool.handler(
        {"active_only": True, "limit": 10}, {"user_id": "user-1"},
    ))

    assert [item["commitment_id"] for item in result] == [own.commitment_id]
    assert result[0]["status"] == "scheduled"
    assert result[0]["version"] == 1
    assert "user_id" not in tool.schema.get("properties", {})
