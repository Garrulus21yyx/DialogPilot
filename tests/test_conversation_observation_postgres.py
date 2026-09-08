"""Recreate runtime and database connections around interrupted observation turns."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import ProposalDisposition, TurnProposal
from application.turn_runtime import TurnRuntime
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from tests.test_turn_runtime import _Executor, _OrderUnderstanding
from tests.test_knowledge_answer_boundary import Verifier


@pytest.mark.parametrize("point", ["after_first_read", "after_second_read", "before_delivery"])
def test_observation_turn_survives_postgres_connection_and_runtime_recreation(postgres_database_url, point):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
        conversation_id="pg-observation-" + uuid4().hex, request_id="original")
    executor, decisions = _Executor(), []
    message = TurnObservations("Check the order, refresh the status, and answer me")

    class Understanding:
        async def __call__(self, observations, state, deterministic, registry, context):
            decisions.append(observations.raw_text)
            if context.observed_execution and len(context.observed_execution.outcome_items) == 2:
                return TurnProposal(ProposalDisposition.RESPOND, (), "ANSWER", response_text="The order has shipped.")
            base = await _OrderUnderstanding()()
            return replace(base, commands=(replace(base.commands[0], observe_result=True),))

    class Interrupted(TurnRuntime):
        async def _execute_work_plan(self, state):
            result = await super()._execute_work_plan(state)
            step = state["prepared"].planning_step
            if (point == "after_first_read" and step == 0 or point == "after_second_read" and step == 1):
                raise RuntimeError("injected interruption after durable child execution")
            return result

        async def _assemble_response(self, state):
            if point == "before_delivery":
                raise RuntimeError("injected interruption before delivery")
            return await super()._assemble_response(state)

    async def run():
        for attempt in range(2):
            pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=2))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(postgres_database_url, setup=attempt == 0) as checkpoint:
                    manager = TargetConversationManager(state_store=PostgresConversationStateStore(pool),
                        registry=build_default_capability_registry("tenant-a"), understanding=Understanding(),
                        orchestration=OrchestrationRuntime(direct_executor=executor, domain_workers={},
                            checkpointer=checkpoint))
                    runtime = (Interrupted if attempt == 0 else TurnRuntime)(manager,
                        ResponseAssembler(knowledge_verifier=Verifier(True)), checkpointer=checkpoint)
                    if attempt == 0:
                        with pytest.raises(RuntimeError, match="injected interruption"):
                            await runtime.execute(identity, message)
                    else:
                        result = await runtime.execute(identity, message)
                        assert result.managed.request_completed
                        assert len(result.managed.board.outcome_items) == 2
                        assert result.assembled.text == "The order has shipped."
                        loaded = manager._state_store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
                        assert loaded.fingerprint == result.managed.state_after.fingerprint
            finally:
                pool.close()
        assert executor.calls == 2
        assert decisions == [message.raw_text] * 3
    asyncio.run(run())


def test_observed_read_then_approval_survives_runtime_recreation(postgres_database_url):
    """The main read, prepared action and approved continuation share one history."""
    from langchain_core.messages import AIMessage
    from application.conversation_agent import ConversationAgent
    from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
    from application.turn_planning import CommandKind, CommandProposal
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from infrastructure.target_tool_execution import TargetToolExecutor
    from infrastructure.target_workflow_execution import TargetWorkflowExecutor
    from mcp.tool_manager import MCPToolManager, Tool, ToolEffectReceipt, ToolEffectStatus
    from tests.test_conversation_agent import Provider
    from tests.test_target_framework_agent import ScriptedToolModel

    PostgresMigrationRunner(postgres_database_url).upgrade()
    registry = build_default_capability_registry("tenant-a")
    action = replace(registry.action("order.cancel:v1"), flow_ref=None)
    owner = replace(registry.agent("order_logistics"), allowed_skill_ids=(),
        allowed_tool_ids=("order_lookup", "order_cancel", "order_cancel_status"))
    registry = replace(registry, agents=(owner,), actions=(action,), flows=(), skills=())
    calls, decisions = [], []
    tools = MCPToolManager("test-key", model="test-model")
    schema = {"type": "object", "properties": {"order_id": {"type": "string"}},
              "required": ["order_id"], "additionalProperties": False}

    async def lookup(params, context):
        calls.append("read")
        return {"order_id": params["order_id"], "status": "paid", "version": 4}

    async def cancel(params, context):
        calls.append("write")
        return ToolEffectReceipt({"cancelled": True}, ToolEffectStatus.COMMITTED, "cancel-receipt")

    tools.register(Tool("order_lookup", "Read order", lookup, schema,
        allowed_agents=("general",), authority="order.current_state"))
    tools.register(Tool("order_cancel_status", "Read cancellation", lookup, schema,
        allowed_agents=("general",), authority="order.cancellation_state"))
    tools.register(Tool("order_cancel", "Cancel order", cancel,
        {**schema, "properties": {**schema["properties"], "expected_order_version": {"type": "integer"}}},
        allowed_agents=("general",), authority="order.cancel_action", read_only=False,
        requires_approval=True, receipt_schema_version="action-receipt-v1"))
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "prepare_order_cancel",
            "args": {"order_id": "DP1234"}, "id": "prepare-cancellation"}]),
        AIMessage(content="Shall I cancel order DP1234?"),
        AIMessage(content="Your cancellation has been completed."),
    ])
    conversation = "pg-observed-approval-" + uuid4().hex

    def identity(request):
        return IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
            conversation_id=conversation, request_id=request)

    class Understanding:
        async def __call__(self, observations, state, deterministic, registry, context):
            decisions.append(observations.raw_text)
            if context.observed_execution is not None:
                assert len(context.observed_execution.outcome_items) == 1
                return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(
                    "cancel", CommandKind.DELEGATE_TASK, owner.agent_id,
                    "Cancel order DP1234 then explain the result", allow_action_proposals=True),), "CANCEL")
            base = await _OrderUnderstanding()()
            return replace(base, commands=(replace(base.commands[0], observe_result=True),))

    async def run():
        pending = None
        for attempt in range(2):
            pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
            pool.open()
            try:
                checkpoint_owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=attempt == 0)
                async with checkpoint_owner as checkpoint:
                    domain = TargetFrameworkAgent(model, tools, review_model=model,
                        review_available_tokens=14200, result_store=checkpoint_owner.store,
                        registry=registry, system_prompt=owner.description)
                    understanding = Understanding() if attempt == 0 else CascadedTargetUnderstanding(
                        StateBoundTargetUnderstanding(), ConversationAgent(Provider({"status": "resolved",
                            "approval_decision": {"approval_id": pending.approval_id, "decision": "approve"}})))
                    manager = TargetConversationManager(state_store=PostgresConversationStateStore(pool),
                        registry=registry, understanding=understanding,
                        orchestration=OrchestrationRuntime(
                            direct_executor=TargetToolExecutor(tools, registry=registry),
                            domain_workers={owner.agent_id: domain},
                            workflow_executor=TargetWorkflowExecutor(pool, tools, registry=registry),
                            checkpointer=checkpoint))
                    runtime = TurnRuntime(manager, ResponseAssembler(), checkpointer=checkpoint)
                    if attempt == 0:
                        first = await runtime.execute(identity("request"), TurnObservations("Cancel order DP1234"))
                        pending = first.managed.state_after.pending_approval
                        assert pending is not None
                        assert "write" not in calls
                        assert len(first.managed.board.outcome_items) == 2
                        assert decisions == ["Cancel order DP1234"] * 2
                    else:
                        approval = TurnObservations("Yes", approval_decision=True, approval_id=pending.approval_id)
                        result = await runtime.execute(identity("approval"), approval)
                        assert result.managed.state_after.pending_approval is None
                        assert result.managed.request_completed
                        assert any(receipt.receipt_id == "cancel-receipt"
                            for outcome in result.managed.board.results for receipt in outcome.action_receipts)
                        replay = await runtime.execute(identity("approval"), approval)
                        assert replay.assembled == result.assembled
                        assert calls.count("write") == 1
                        assert model.calls == 3
            finally:
                pool.close()
    asyncio.run(run())
