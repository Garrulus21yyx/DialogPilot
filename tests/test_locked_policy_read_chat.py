"""Two locked positive cases traverse the real read-only policy chain."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from application.chat_application import Completed
from evaluation.command_primary_eval.locked_conversation import (
    LockedConversationTransportAdapter,
)
from tests.support.locked_policy_read_harness import (
    build_locked_policy_read_harness,
)


DATASET = Path("data/eval/dialogpilot-synthetic-contract-v1")


def _case(case_id: str) -> dict:
    rows = (
        json.loads(line)
        for line in (DATASET / "cases.jsonl").read_text().splitlines()
        if line.strip()
    )
    return next(value for value in rows if value["case_id"] == case_id)


@pytest.mark.parametrize("case_id", ("dp-policy-01-a", "dp-policy-01-b"))
def test_locked_refund_eligibility_uses_two_authoritative_reads(
    case_id,
    tmp_path,
    fresh_postgres_database_url,
):
    case = _case(case_id)
    harness = build_locked_policy_read_harness(
        case["initial_state"],
        sqlite_path=str(tmp_path / f"{case_id}.db"),
        postgres_url=fresh_postgres_database_url,
    )
    try:
        adapter = LockedConversationTransportAdapter(harness.application, DATASET)
        turn = asyncio.run(adapter.run(case_id))[0]
        assert isinstance(turn.outcome, Completed)

        response = turn.outcome.response
        payload = json.loads(response["response"].removeprefix("查询结果："))
        expected = case["expected_outcome"]["expected_business_state"]
        assert payload["order_lookup"]["order_id"] == expected["order_id"]
        assert payload["order_lookup"]["status"] == expected["status"]
        assert payload["refund_eligibility_check"]["order_id"] == (expected["order_id"])
        assert (
            payload["refund_eligibility_check"]["reason_code"]
            == (expected["refund_eligibility"])
        )
        assert [record.tool_name for record in harness.tools.audit_records()] == [
            "order_lookup",
            "refund_eligibility_check",
        ]
        assert response["coverage"]["complete"] is True
        assert [
            value["requirement_id"] for value in response["coverage"]["requirements"]
        ] == ["order.current_state", "refund.eligibility"]
        assert len(response["coverage"]["evidence_receipts"]) == 2
        assert len(response["agent_outcomes"][0]["tool_receipts"]) == 2
        assert len(response["task_plan"]["tasks"]) == 1
        assert all(record.read_only for record in harness.tools.audit_records())

        state = harness.flow_store.load(harness.principal)
        assert state.aggregate.version == 1
        assert len(state.active_flows) == 1
        flow = state.active_flows[0]
        assert flow.definition.flow_id == "refund_eligibility"
        assert {binding.name: binding.value for binding in flow.bindings} == {
            "order_id": "order-1",
        }

        prompt = harness.messages.requests[0]["messages"][0]["content"]
        assert "expected_outcome" not in prompt
        stages = {value.stage: value for value in turn.outcome.stages}
        assert stages["intent"].status.value == "skipped"
        assert stages["knowledge_retrieval"].detail["used"] is False
        assert stages["flow_transition"].detail == {"status": "applied"}
        stage_order = [value.stage for value in turn.outcome.stages]
        assert stage_order.index("flow_transition") < stage_order.index("verification")
        assert stage_order.index("verification") < stage_order.index("delivery")
    finally:
        harness.close()
