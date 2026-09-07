from langgraph.store.memory import InMemoryStore
"""Frozen counterfactual inputs traverse Target and PostgreSQL owners."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from application.chat_contracts import Completed
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal
from application.work_item import ArgumentValue
from evaluation.command_primary_eval.locked_conversation import LockedConversationTransportAdapter
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from infrastructure.target_chat_adapters import PostgresTargetAdmission, PostgresTargetPublication
from infrastructure.target_framework_agent import TargetFrameworkAgent
from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager
from services.customer_operations import CustomerOperationsService, OrderStatus
from tests.test_target_framework_agent import ScriptedToolModel


DATASET = Path("data/eval/dialogpilot-synthetic-contract-v1")


@pytest.mark.parametrize("case_id", ("dp-policy-01-a", "dp-policy-01-b"))
def test_locked_refund_eligibility_uses_two_authoritative_reads(
    case_id, fresh_postgres_database_url,
):
    case = next(row for row in (
        json.loads(line) for line in (DATASET / "cases.jsonl").read_text().splitlines()
        if line.strip()
    ) if row["case_id"] == case_id)
    initial = case["initial_state"]
    business = initial["business_state"]
    tenant, user, conversation = (
        initial[key] for key in ("tenant_id", "user_id", "conversation_id")
    )
    PostgresMigrationRunner(fresh_postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(fresh_postgres_database_url, min_size=1, max_size=4))
    pool.open()
    try:
        owner = CustomerOperationsService(
            pool, tenant_id=tenant,
            clock=lambda: datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
        )
        owner.upsert_order(
            order_id=business["order_id"], user_id=user, item_name="fixture-item",
            amount_minor=10000, currency="CNY", status=OrderStatus(business["status"]),
            refundable_until="2026-09-15T00:00:00+00:00",
        )
        tools = MCPToolManager("test", model="test")
        for tool in customer_operation_tools(owner):
            tools.register(tool)

        class Model(ScriptedToolModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if self.calls < 2:
                    return super()._generate(messages, stop, run_manager, **kwargs)
                self.calls += 1
                # Render observed artifacts, never expected_outcome or seeded eligibility.
                payload = {
                    message.name: message.artifact["result"]["data"]
                    for message in messages if isinstance(message, ToolMessage)
                }
                return ChatResult(generations=[ChatGeneration(message=AIMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                ))])

        model = Model(responses=[AIMessage(content="", tool_calls=[{
            "name": name, "args": {"order_id": business["order_id"]},
            "id": f"read-{index}",
        }]) for index, name in enumerate(("order_lookup", "refund_eligibility_check"))])
        registry = build_default_capability_registry(tenant)
        worker = TargetFrameworkAgent(model, tools, result_store=InMemoryStore(), registry=registry, system_prompt="核验退款资格。")
        planning_calls = []

        async def understanding(observations, state, deterministic, registry, turn_context):
            planning_calls.append(observations.raw_text)
            return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(
                "eligibility", CommandKind.DELEGATE_TASK, "billing_refund",
                observations.raw_text,
                arguments=(ArgumentValue.create("order_id", business["order_id"]),),
                requirement_ids=("order.current_state", "refund.eligibility"),
            ),), "FIXTURE_PLANNING")

        async def forbidden(_context):
            raise AssertionError("delegated query must not execute another path")

        state_store = PostgresConversationStateStore(pool)
        application = TargetChatApplication(
            manager=TargetConversationManager(
                state_store=state_store, registry=registry, understanding=understanding,
                orchestration=OrchestrationRuntime(
                    direct_executor=forbidden, domain_workers={"billing_refund": worker},
                ),
            ),
            admission=PostgresTargetAdmission(pool),
            publication=PostgresTargetPublication(PostgresResponseDeliveryService(
                pool, resume_binding_secret="locked-test-secret",
            )),
            bundle_version=registry.bundle_version,
        )
        adapter = LockedConversationTransportAdapter(application, DATASET)
        first = asyncio.run(adapter.run(case_id))[0]
        replay = asyncio.run(adapter.run(case_id))[0]
        assert isinstance(first.outcome, Completed), first.outcome
        assert isinstance(replay.outcome, Completed)
        assert first.score_eligible is False
        assert replay.outcome.response_id == first.outcome.response_id
        response = first.outcome.response
        payload = json.loads(response["response"])
        expected = case["expected_outcome"]["expected_business_state"]
        assert payload["order_lookup"]["order_id"] == expected["order_id"]
        assert payload["order_lookup"]["status"] == expected["status"]
        assert payload["refund_eligibility_check"]["order_id"] == expected["order_id"]
        assert payload["refund_eligibility_check"]["reason_code"] == expected["refund_eligibility"]
        assert [record.tool_name for record in tools.audit_records()] == [
            "order_lookup", "refund_eligibility_check",
        ]
        assert all(record.read_only for record in tools.audit_records())
        assert response["coverage"]["complete"] is True
        assert response["verification_status"] == "pass"
        assert len(response["task_plan"]["work_item_ids"]) == 1
        assert len(planning_calls) == 1
        assert model.calls == 3
        assert state_store.load(tenant, user, conversation).workstreams == ()
    finally:
        pool.close()
