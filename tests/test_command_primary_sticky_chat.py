"""Positive sticky read-only turn through the real chat lifecycle."""
from __future__ import annotations

import asyncio

from application.chat_contracts import Completed
from tests.support.sticky_read_only_harness import (
    STICKY_COMMAND,
    build_sticky_read_only_harness,
)


def test_sticky_refund_status_uses_state_then_one_read_only_tool(customer_operations):
    harness = build_sticky_read_only_harness(customer_operations)

    outcome = asyncio.run(harness.application.handle(STICKY_COMMAND))

    assert isinstance(outcome, Completed)
    assert harness.semantic.calls == 1
    assert harness.flow_state_probe(STICKY_COMMAND, outcome) == {
        "aggregate_version": 2,
        "flow_id": "refund_status",
        "flow_state_version": 4,
    }
    assert [record.tool_name for record in harness.tools.audit_records()] == [
        "refund_status",
    ]
    assert outcome.response["intent"] == "refund"
    stages = {stage.stage: stage for stage in outcome.stages}
    assert stages["intent"].status.value == "skipped"
    assert stages["flow_transition"].detail == {"status": "applied"}
    assert harness.events == [
        "state",
        "active_case",
        "verification",
        "delivery",
        "memory_write",
    ]
