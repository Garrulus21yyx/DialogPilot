import asyncio
import json
from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.conversation_state import InMemoryConversationStateStore
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_conversation_manager import TargetConversationManager
from application.turn_runtime import TurnRuntime
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
)
from application.work_item import ArgumentValue
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer


class _Executor:
    def __init__(self):
        self.calls = 0

    async def __call__(self, context):
        self.calls += 1
        order_id = dict(
            (item.name, item.value) for item in context.work_item.arguments
        )["order_id"]
        fact = FactRecord(
            f"order:{order_id}", "order.current_state",
            json.dumps({"status": "SHIPPED"}, separators=(",", ":")),
            FactSourceKind.VERIFIED_STATE, "receipt:order-read",
            "order_lookup", "v1", datetime.now(timezone.utc),
        )
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "ORDER_FOUND",
            "executor-v1",
            facts=(fact,),
            candidate_response=f"订单 {order_id} 已发货。",
        )


class _OrderUnderstanding:
    async def __call__(self, *_args, **_kwargs):
        return TurnProposal(
            ProposalDisposition.RESOLVED,
            (CommandProposal(
                "order-status",
                CommandKind.DIRECT_TOOL,
                "order_logistics",
                "Query order status",
                (ArgumentValue.create("order_id", "DP1234"),),
                ("order.current_state",),
                tool_id="order_lookup",
            ),),
            "TEST_ORDER_PLAN",
        )
class _CountingManager:
    def __init__(self, manager):
        self.manager = manager
        self.prepare_calls = 0
        self.execute_calls = 0

    async def prepare(self, *args, **kwargs):
        self.prepare_calls += 1
        return await self.manager.prepare(*args, **kwargs)

    async def execute(self, prepared):
        self.execute_calls += 1
        return await self.manager.execute(prepared)


class _FailOnceAssembler:
    def __init__(self):
        self.calls = 0
        self.delegate = ResponseAssembler()

    async def assemble(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("injected assembly crash")
        return await self.delegate.assemble(*args, **kwargs)


def _identity():
    return IdentityFactory().create_invocation(
        tenant_id="tenant-a", user_id="user-a",
        conversation_id="conversation-a", request_id="request-a",
    )


def test_turn_graph_resumes_at_assembly_without_replanning_or_reexecuting_tools():
    executor = _Executor()
    manager = _CountingManager(TargetConversationManager(
        state_store=InMemoryConversationStateStore(),
        registry=build_default_capability_registry("tenant-a"),
        understanding=_OrderUnderstanding(),
        orchestration=OrchestrationRuntime(
            direct_executor=executor, domain_workers={},
        ),
    ))
    assembler = _FailOnceAssembler()
    runtime = TurnRuntime(
        manager,
        assembler,
        checkpointer=InMemorySaver(serde=target_checkpoint_serializer()),
    )

    with pytest.raises(RuntimeError, match="injected assembly crash"):
        asyncio.run(runtime.execute(
            _identity(), TurnObservations("查询订单 DP1234"),
        ))

    result = asyncio.run(runtime.execute(
        _identity(), TurnObservations("查询订单 DP1234"),
    ))

    assert result.assembled.text == "订单 DP1234 已发货。"
    assert manager.prepare_calls == 1
    assert manager.execute_calls == 1
    assert executor.calls == 1
    assert assembler.calls == 2

    replay = asyncio.run(runtime.execute(
        _identity(), TurnObservations("查询订单 DP1234"),
    ))
    assert replay == result
    assert executor.calls == 1
    assert assembler.calls == 2
