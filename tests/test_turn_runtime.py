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


class _FailOncePrepareManager(_CountingManager):
    async def prepare(self, *args, **kwargs):
        self.prepare_calls += 1
        if self.prepare_calls == 1:
            raise RuntimeError("injected prepare crash")
        return await self.manager.prepare(*args, **kwargs)


class _CrashAfterExecutionManager(_CountingManager):
    async def execute(self, prepared):
        self.execute_calls += 1
        result = await self.manager.execute(prepared)
        if self.execute_calls == 1:
            raise RuntimeError("injected post-execution crash")
        return result


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


def _manager(executor, *, checkpointer=None, context_provider=None):
    return TargetConversationManager(
        state_store=InMemoryConversationStateStore(),
        registry=build_default_capability_registry("tenant-a"),
        understanding=_OrderUnderstanding(),
        context_provider=context_provider,
        orchestration=OrchestrationRuntime(
            direct_executor=executor,
            domain_workers={},
            checkpointer=checkpointer,
        ),
    )


def test_turn_graph_retries_failed_prepare_without_executing_work_early():
    executor = _Executor()
    manager = _FailOncePrepareManager(_manager(executor))
    runtime = TurnRuntime(
        manager,
        ResponseAssembler(),
        checkpointer=InMemorySaver(serde=target_checkpoint_serializer()),
    )

    with pytest.raises(RuntimeError, match="injected prepare crash"):
        asyncio.run(runtime.execute(
            _identity(), TurnObservations("查询订单 DP1234"),
        ))

    result = asyncio.run(runtime.execute(
        _identity(), TurnObservations("查询订单 DP1234"),
    ))
    assert result.assembled.text == "订单 DP1234 已发货。"
    assert manager.prepare_calls == 2
    assert manager.execute_calls == 1
    assert executor.calls == 1


def test_turn_graph_reuses_completed_work_plan_after_outer_execution_crash():
    executor = _Executor()
    checkpointer = InMemorySaver(serde=target_checkpoint_serializer())
    manager = _CrashAfterExecutionManager(
        _manager(executor, checkpointer=checkpointer),
    )
    runtime = TurnRuntime(
        manager,
        ResponseAssembler(),
        checkpointer=checkpointer,
    )

    with pytest.raises(RuntimeError, match="injected post-execution crash"):
        asyncio.run(runtime.execute(
            _identity(), TurnObservations("查询订单 DP1234"),
        ))

    result = asyncio.run(runtime.execute(
        _identity(), TurnObservations("查询订单 DP1234"),
    ))
    assert result.assembled.text == "订单 DP1234 已发货。"
    assert manager.prepare_calls == 1
    assert manager.execute_calls == 2
    assert executor.calls == 1


def test_turn_graph_resumes_at_assembly_without_replanning_or_reexecuting_tools():
    executor = _Executor()
    manager = _CountingManager(_manager(executor))
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


def test_authenticated_context_is_pinned_across_recovery_and_cannot_replace_identity():
    seen = []
    class Executor(_Executor):
        async def __call__(self, context):
            seen.append(dict(context.trusted_context))
            return await super().__call__(context)
    runtime = TurnRuntime(_manager(Executor()), ResponseAssembler(),
                          checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
    context = {'authorization_fingerprint': 'auth-a', 'cache_scope': 'policy-a',
               'tenant_id': 'cannot-override-identity', 'retrieval_policy': {'top_k': 5}}
    asyncio.run(runtime.execute(_identity(), TurnObservations('查询订单 DP1234'), execution_context=context))
    asyncio.run(runtime.execute(_identity(), TurnObservations('查询订单 DP1234'),
                                execution_context={**context, 'cache_scope': 'policy-b'}))
    assert len(seen) == 1
    assert seen[0]['tenant_id'] == 'tenant-a'
    assert seen[0]['cache_scope'] == 'policy-a'
    assert seen[0]['authorization_fingerprint'] == 'auth-a'
    with pytest.raises(ValueError, match='authorization changed'):
        asyncio.run(runtime.execute(_identity(), TurnObservations('查询订单 DP1234'),
                                    execution_context={**context, 'authorization_fingerprint': 'auth-b'}))


def test_loaded_context_survives_assembly_retry_without_reloading():
    from application.target_conversation_manager import TargetTurnContext, TargetContextMessage, TargetContextProjectionStatus
    from application.conversation_context import conversation_context_payload
    context = TargetTurnContext(recent_messages=(
        TargetContextMessage('user', '耳机拆封了', 'turn:1', 1),
        TargetContextMessage('assistant', '是质量问题吗？', 'turn:2', 2),
    ), projection_status=TargetContextProjectionStatus.READY, source_watermark=2, projection_reason_codes=())
    class Context:
        calls = 0
        async def load(self, *args):
            self.calls += 1
            return context
    class Assembler(_FailOnceAssembler):
        def __init__(self):
            super().__init__()
            self.contexts = []
        async def assemble(self, *args, **kwargs):
            self.contexts.append(kwargs['conversation_context'])
            return await super().assemble(*args, **kwargs)
    provider, assembler = Context(), Assembler()
    runtime = TurnRuntime(_manager(_Executor(), context_provider=provider), assembler,
                          checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
    with pytest.raises(RuntimeError, match='injected assembly crash'):
        asyncio.run(runtime.execute(_identity(), TurnObservations('不是，查订单 DP1234')))
    asyncio.run(runtime.execute(_identity(), TurnObservations('不是，查订单 DP1234')))
    assert provider.calls == 1
    assert assembler.contexts == [conversation_context_payload(context)] * 2
