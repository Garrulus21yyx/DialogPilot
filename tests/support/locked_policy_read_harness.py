"""Real-owner harness for the two locked refund-eligibility reads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping

from application.active_case import (
    ActiveCaseContextView,
    ActiveCaseProjection,
    ActiveCaseSelection,
    ActiveCaseState,
)
from application.chat_application import ChatApplication, ChatOperations, ChatServices
from application.turn_state import PrincipalScope
from core.model_policy import ModelProfile
from infrastructure.command_primary_runtime import build_command_primary_chat_planner
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_flow_state import PostgresFlowStateStore
from mcp.customer_operations_tools import customer_operation_tools
from mcp.tool_manager import MCPToolManager
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.customer_operations import CustomerOperationsService, OrderStatus
from services.response_delivery import DeliveryStatus


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


class RefundEligibilityMessages:
    """Provider transport fake; production parsing and Registry validation remain real."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(
            id="refund-eligibility-command",
            content=[
                SimpleNamespace(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "RESOLVED",
                            "commands": [
                                {
                                    "kind": "START_FLOW",
                                    "source_flow_instance_id": None,
                                    "target_flow_id": "refund_eligibility",
                                    "target_flow_version": "v1",
                                }
                            ],
                        }
                    ),
                )
            ],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


@dataclass
class LockedPolicyReadHarness:
    application: ChatApplication
    tools: MCPToolManager
    flow_store: PostgresFlowStateStore
    principal: PrincipalScope
    messages: RefundEligibilityMessages
    pool: PostgresPool

    def close(self) -> None:
        self.pool.close()


def build_locked_policy_read_harness(
    initial_state: Mapping[str, Any],
    *,
    sqlite_path: str,
    postgres_url: str,
) -> LockedPolicyReadHarness:
    """Seed owners from locked initial_state without consulting expected output."""

    PostgresMigrationRunner(postgres_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_url, min_size=1, max_size=2))
    pool.open()
    principal = PrincipalScope(
        str(initial_state["tenant_id"]),
        str(initial_state["user_id"]),
        str(initial_state["conversation_id"]),
    )
    with pool.transaction() as connection:
        connection.execute(
            """
            INSERT INTO dialogpilot_app.conversations (
                tenant_id,user_id,conversation_id
            ) VALUES (%s,%s,%s)
            """,
            (principal.tenant_id, principal.user_id, principal.conversation_id),
        )

    business = dict(initial_state["business_state"])
    operations_owner = CustomerOperationsService(
        sqlite_path,
        clock=lambda: NOW,
    )
    operations_owner.upsert_order(
        order_id=str(business["order_id"]),
        user_id=principal.user_id,
        item_name="locked-fixture-item",
        amount_minor=10000,
        currency="CNY",
        status=OrderStatus(str(business["status"])),
        refundable_until="2026-09-15T00:00:00+00:00",
    )
    tools = MCPToolManager(api_key="test", model="test")
    for tool in customer_operation_tools(operations_owner):
        tools.register(tool)

    class Orchestrator:
        async def recognize_intent(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("policy primary must not call legacy Intent")

        async def run(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("compiled policy read must not rerun Agent planning")

    class Memory:
        async def get_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                recent_messages=[],
                retrieval_hits=[],
                to_sections=lambda: [],
            )

        async def add_messages(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class Delivery:
        @staticmethod
        def select_response(**_kwargs: Any) -> Any:
            return SimpleNamespace(
                response_id="policy-read-response",
                seq=1,
                status=DeliveryStatus.SELECTED,
            )

    async def active_case(*_args: Any, **_kwargs: Any) -> ActiveCaseContextView:
        return ActiveCaseContextView(
            ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
            ActiveCaseSelection((), ()),
        )

    async def verify(*_args: Any, **kwargs: Any) -> VerificationResult:
        if not kwargs["coverage"]["complete"]:
            raise AssertionError("policy read reached publication without coverage")
        return VerificationResult(
            VerificationStatus.PASS,
            True,
            False,
            "authoritative policy read",
            VerificationReasonCode.PASSED,
        )

    async def done(**_kwargs: Any) -> None:
        return None

    messages = RefundEligibilityMessages()
    orchestrator = Orchestrator()
    planner = build_command_primary_chat_planner(
        orchestrator,
        {"COMMAND_PRIMARY_MODE": "structured_read_only_primary"},
        postgres_pool=pool,
        command_completion_client=SimpleNamespace(messages=messages),
        command_model_profile=ModelProfile("command-router-test"),
    )
    services = ChatServices(
        orchestrator=orchestrator,
        memory=Memory(),
        answer_verifier=object(),
        ticket_service=object(),
        response_delivery=Delivery(),
        context_assembler=SimpleNamespace(
            assemble=lambda **_kwargs: SimpleNamespace(system_context=""),
        ),
        bundle_registry=object(),
        bundle_resolver=SimpleNamespace(
            resolve=lambda _user_id: SimpleNamespace(
                primary=SimpleNamespace(version="locked-policy-read-v1"),
                pinned_refs=None,
            )
        ),
        tool_manager=tools,
        command_primary_chat_planner=planner,
    )
    chat_operations = ChatOperations(
        active_ticket_context=active_case,
        build_knowledge_context=lambda *_args, **_kwargs: _unexpected("Knowledge RAG"),
        capture_badcases=done,
        handoff_priority=lambda *_args: None,
        policy_terminal_verification=lambda _result: None,
        publish_candidate=lambda candidate, _verification: candidate,
        public_agent_outcomes=lambda outcomes: outcomes,
        record_intent_prediction=lambda **_kwargs: _unexpected("legacy Intent"),
        select_publication_candidate=lambda candidate, _evidence, **_kwargs: (
            candidate,
            False,
        ),
        trace_id=lambda: "locked-policy-read-trace",
        verify_for_publication=verify,
    )
    return LockedPolicyReadHarness(
        ChatApplication(services, chat_operations),
        tools,
        PostgresFlowStateStore(pool),
        principal,
        messages,
        pool,
    )


async def _unexpected(owner: str) -> Any:
    raise AssertionError(f"policy read unexpectedly invoked {owner}")
