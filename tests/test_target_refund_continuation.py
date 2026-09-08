"""Contextual read continuation uses Target, not a persistent read-only Flow."""
import asyncio
import pytest
from dataclasses import replace
from datetime import datetime, timezone

from application.chat_contracts import ChatCommand, Completed
from application.conversation_agent import ConversationAgent
from application.conversation_state import InMemoryConversationStateStore
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import (
    TargetContextMessage, TargetContextProjectionStatus,
    TargetConversationManager, TargetTurnContext,
)
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from evaluation.chat_application_runner import ChatApplicationRunner
from infrastructure.target_tool_execution import TargetToolExecutor
from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager
from services.customer_operations import CustomerOperationsService, OrderStatus
from tests.test_target_chat_cutover import _Admission, _Publication


@pytest.mark.parametrize('has_application', [False, True])
def test_contextual_refund_read_replays_once_but_new_turn_refreshes(customer_operations, has_application):
    owner = CustomerOperationsService(
        customer_operations.pool, tenant_id="tenant-a",
        clock=lambda: datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
    )
    order = owner.upsert_order(
        order_id="DP1234", user_id="user-a", item_name="keyboard",
        amount_minor=39900, currency="CNY", status=OrderStatus.DELIVERED,
        refundable_until="2026-09-15T00:00:00+00:00",
    )
    original = None
    if has_application:
        original, created = owner.create_refund_request(
            idempotency_key="fixture-refund", user_id="user-a", order_id=order.order_id,
            expected_order_version=order.version, reason="fixture",
        )
        assert created is True
    tools = MCPToolManager("test", model="test")
    for tool in customer_operation_tools(owner):
        tools.register(tool)
    calls = []

    class Context:
        async def load(self, *_args):
            return TargetTurnContext(
                recent_messages=(TargetContextMessage(
                    "user", "订单 DP1234 的退款还没有收到", "transcript:prior-user", 1,
                ),),
                projection_status=TargetContextProjectionStatus.READY,
                source_watermark=1, projection_reason_codes=(),
            )

    class Provider:
        version = "contextual-read-fixture-v1"

        async def plan(self, payload):
            calls.append(payload)
            assert "DP1234" in str(payload["conversation_context"])
            reference = next(candidate for group in payload['entity_bindings']
                             if group['field_name'] == 'reference' for candidate in group['candidates']
                             if candidate['value'] == 'DP1234')
            return {"status": "resolved", "goals": [{
                "kind": "refund_status", "order_id": "DP1234",
                "order_id_source_ref": reference['source_ref'],
            }]}

    registry = build_default_capability_registry("tenant-a")
    states = InMemoryConversationStateStore()
    application = TargetChatApplication(
        manager=TargetConversationManager(
            state_store=states, registry=registry, context_provider=Context(),
            understanding=CascadedTargetUnderstanding(
                StateBoundTargetUnderstanding(), ConversationAgent(Provider()),
            ),
            orchestration=OrchestrationRuntime(
                direct_executor=TargetToolExecutor(tools, registry=registry), domain_workers={},
            ),
        ),
        admission=_Admission(), publication=_Publication(),
        bundle_version=registry.bundle_version,
    )
    runner = ChatApplicationRunner(
        lambda _overrides: application,
        state_probes={"refund": lambda _command, _outcome: {
            "value": owner.lookup_refund_status(user_id="user-a", order_id="DP1234").request,
        }},
    )
    command = ChatCommand("还是没到账", "user-a", "tenant-a", "conversation-a", "read-1")
    first = asyncio.run(runner.run(command))
    replay = asyncio.run(runner.run(command))
    assert isinstance(first.outcome, Completed), first.outcome
    assert isinstance(replay.outcome, Completed)
    assert replay.outcome.response_id == first.outcome.response_id
    assert len(calls) == 1
    assert len(tools.audit_records()) == 1
    refreshed = asyncio.run(runner.run(replace(command, request_id="read-2")))
    assert isinstance(refreshed.outcome, Completed)
    assert refreshed.outcome.response_id != first.outcome.response_id
    assert len(calls) == 2
    assert [record.tool_name for record in tools.audit_records()] == ["refund_status"] * 2
    assert all(record.read_only for record in tools.audit_records())
    for run in (first, replay, refreshed):
        assert run.public_response["coverage"]["complete"] is True
        assert run.public_response["routing_disposition"] == "direct"
        assert run.owner_state["refund"]["value"] == original
    assert states.load("tenant-a", "user-a", "conversation-a").workstreams == ()
