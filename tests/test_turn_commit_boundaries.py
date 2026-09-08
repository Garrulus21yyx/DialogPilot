"""Failure-position tests over the same durable turn, without model requests."""
import asyncio
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.conversation_agent import ConversationAgent
from application.conversation_state import ConversationStateConflict
from application.deterministic_resolution import TurnObservations, ResolutionKind
from application.response_assembly import ResponseAssembler
from application.target_conversation_manager import TargetTurnContext
from application.turn_runtime import TurnRuntime
from core.framework_models import ModelInvocationError
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_knowledge_answer_boundary import Verifier
from tests.test_target_chat_cutover import _MissingThenReadExecutor, _default_understanding
from tests.test_turn_runtime import _CountingManager, _Executor, _identity, _manager


class Author:
    async def compose(self, payload):
        return "\n".join([('请提供核验信息。')])


class CrashBoundary(_CountingManager):
    def __init__(self, manager, boundary):
        super().__init__(manager)
        self.boundary, self.crashed = boundary, False

    def crash(self, boundary):
        if boundary == self.boundary and not self.crashed:
            self.crashed = True
            raise RuntimeError("injected boundary crash")

    async def prepare(self, *args, **kwargs):
        result = await super().prepare(*args, **kwargs)
        self.crash("prepared")
        return result

    async def execute(self, prepared):
        result = await super().execute(prepared)
        self.crash("executed")
        return result

    async def commit(self, result):
        await super().commit(result)
        self.crash("committed")


@pytest.mark.parametrize("boundary", ["context", "prepared", "executed", "committed"])
def test_field_reply_survives_each_turn_boundary_without_losing_original_target(boundary):
    executor = _MissingThenReadExecutor()
    saver = InMemorySaver(serde=target_checkpoint_serializer())
    class Context:
        armed = False
        async def load(self, *args):
            if self.armed:
                self.armed = False
                raise RuntimeError("injected boundary crash")
            return TargetTurnContext()
    context = Context()
    manager = _manager(executor, checkpointer=saver, context_provider=context)
    manager._understanding = _default_understanding()
    assembler = ResponseAssembler(Author(), knowledge_verifier=Verifier(True))
    async def run():
        first = await TurnRuntime(manager, assembler, checkpointer=saver).execute(
            _identity(), TurnObservations("查询订单 DP1234"))
        pending = first.managed.state_after.pending_interaction
        assert pending is not None
        identity = IdentityFactory().create_invocation(
            tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
            request_id="reply")
        observation = TurnObservations(
            "REF-9", interaction_id=pending.interaction_id,
            interaction_version=pending.version,
            interaction_values=((pending.requested_fields[0].target_work_item_id,
                                 "verification_reference", "REF-9"),))
        wrapper = CrashBoundary(manager, boundary)
        runtime = TurnRuntime(wrapper, assembler, checkpointer=saver)
        context.armed = boundary == "context"
        with pytest.raises(RuntimeError, match="injected boundary"):
            await runtime.execute(identity, observation)
        if boundary in {"context", "prepared"}:
            assert manager._state_store.load("tenant-a", "user-a", "conversation-a").pending_interaction == pending
        result = await runtime.execute(identity, observation)
        assert result.managed.deterministic.kind is ResolutionKind.FILL_PENDING_INPUT
        assert result.managed.checkpoint_thread_id == pending.checkpoint_thread_id
        assert result.managed.state_after.pending_interaction is None
        assert manager._state_store.load("tenant-a", "user-a", "conversation-a").fingerprint == result.managed.state_after.fingerprint
        assert executor.calls == [{"order_id": "DP1234"},
                                  {"order_id": "DP1234", "verification_reference": "REF-9"}]
    asyncio.run(run())


def test_pending_creation_commit_replays_exact_checkpointed_decision():
    saver = InMemorySaver(serde=target_checkpoint_serializer())
    executor = _MissingThenReadExecutor()
    manager = CrashBoundary(_manager(executor, checkpointer=saver), "committed")
    runtime = TurnRuntime(manager, ResponseAssembler(Author(), knowledge_verifier=Verifier(True)),
                          checkpointer=saver)
    async def run():
        with pytest.raises(RuntimeError, match="injected boundary"):
            await runtime.execute(_identity(), TurnObservations("查询订单 DP1234"))
        before = manager.manager._state_store.load("tenant-a", "user-a", "conversation-a")
        assert before.pending_interaction is not None
        result = await runtime.execute(_identity(), TurnObservations("查询订单 DP1234"))
        assert result.managed.state_after.fingerprint == before.fingerprint
        assert manager.prepare_calls == manager.execute_calls == len(executor.calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("prefix", [0, 1, 2, 3])
def test_state_commit_accepts_exact_prefix_only_and_is_idempotent(prefix):
    from tests.test_conversation_state_resolution import _workstream
    manager = _manager(_Executor())
    initial = manager._state_store.load("tenant-a", "user-a", "conversation-a")
    states = [initial]
    for index in range(3):
        states.append(states[-1].start_workstream(_workstream(f"work-{index}")))
    for before, after in zip(states[:prefix], states[1:prefix + 1]):
        assert manager._state_store.compare_and_set(before, after)
    manager._commit_states(initial, tuple(states[1:]))
    manager._commit_states(initial, tuple(states[1:]))
    assert manager._state_store.load("tenant-a", "user-a", "conversation-a") == states[-1]
    other = states[-1].start_workstream(_workstream("unrelated"))
    assert manager._state_store.compare_and_set(states[-1], other)
    with pytest.raises(ConversationStateConflict):
        manager._commit_states(initial, tuple(states[1:]))
    assert manager._state_store.load("tenant-a", "user-a", "conversation-a") == other


@pytest.mark.parametrize("transient", [True, False])
def test_provider_failure_checkpoint_obeys_sdk_retryability(transient):
    from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
    from application.turn_planning import PlanningUnavailable
    class Provider:
        calls = 0
        async def plan(self, payload):
            self.calls += 1
            if self.calls == 1:
                cause = TimeoutError("temporary") if transient else ValueError("permanent")
                raise ModelInvocationError("conversation_plan", cause)
            return {"status": "out_of_scope"}
    provider = Provider()
    manager = _manager(_Executor())
    manager._understanding = CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(), ConversationAgent(provider))
    runtime = TurnRuntime(manager, ResponseAssembler(),
        checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
    async def run():
        if transient:
            with pytest.raises(ModelInvocationError):
                await runtime.execute(_identity(), TurnObservations("帮我处理一下"))
        else:
            # Permanent understanding failure has no accepted plan. The run/API
            # owner handles terminal delivery; do not fabricate a successful
            # graph completion containing a failure-shaped business plan.
            with pytest.raises(PlanningUnavailable) as failure:
                await runtime.execute(_identity(), TurnObservations("帮我处理一下"))
            assert failure.value.reason_code == "CONVERSATION_PROVIDER_FAILURE"
            snapshot = await runtime.graph.aget_state({"configurable": {
                "thread_id": "turn:" + str(_identity().invocation_key)}})
            assert snapshot.values.get("prepared") is None
            assert snapshot.values.get("managed") is None
            assert provider.calls == 1
            return
        second = await runtime.execute(_identity(), TurnObservations("帮我处理一下"))
        assert second.managed.plan.route.reason_code != "CONVERSATION_PROVIDER_FAILURE"
        assert provider.calls == 2
    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approved", "declined", "expired"])
@pytest.mark.parametrize("boundary", ["context", "prepared", "executed", "committed"])
def test_approval_decision_survives_all_turn_boundaries(decision, boundary):
    from application.conversation_state import InMemoryConversationStateStore
    from application.default_capability_registry import build_default_capability_registry
    from application.orchestration_runtime import OrchestrationRuntime
    from application.target_conversation_manager import TargetConversationManager
    from tests.test_target_persistence_and_manager import (
        _identity as identity, _ResumeAwareUnderstanding, _prepare_workflow_proposal,
        _OrderCancellationPreparationExecutor, _OrderCancellationWorkflowExecutor)
    saver = InMemorySaver(serde=target_checkpoint_serializer())
    store = InMemoryConversationStateStore()
    workflow = _OrderCancellationWorkflowExecutor()
    class Context:
        armed = False
        async def load(self, *args):
            if self.armed:
                self.armed = False
                raise RuntimeError("injected boundary crash")
            return TargetTurnContext()
    context = Context()
    manager = TargetConversationManager(
        state_store=store, registry=build_default_capability_registry("tenant-target"),
        context_provider=context,
        understanding=_ResumeAwareUnderstanding(_prepare_workflow_proposal(
            command_id="prepare", owner="order_logistics", objective="Cancel order",
            arguments=(("order_id", "DP1234"),), requirement_id="order.current_state",
            flow_ref="cancel_order:v1", action_ref="order.cancel:v1", target_entity_ref="order:DP1234")),
        orchestration=OrchestrationRuntime(
            direct_executor=_OrderCancellationPreparationExecutor(), domain_workers={},
            workflow_executor=workflow, checkpointer=saver))
    async def run():
        first = await manager.handle(identity("prepare"), TurnObservations("取消订单 DP1234"))
        before = first.state_after
        if decision == "expired":
            expired = replace(before, version=before.version + 1,
                pending_approval=replace(before.pending_approval, expires_at="2000-01-01T00:00:00+00:00"))
            assert store.compare_and_set(before, expired)
            before = expired
        pending = before.pending_approval
        observation = TurnObservations("",
            approval_decision=decision != "declined", approval_id=pending.approval_id)
        wrapper = CrashBoundary(manager, boundary)
        runtime = TurnRuntime(wrapper, ResponseAssembler(), checkpointer=saver)
        context.armed = boundary == "context"
        with pytest.raises(RuntimeError, match="injected boundary"):
            await runtime.execute(identity("decision"), observation)
        if boundary in {"context", "prepared"}:
            assert store.load("tenant-target", "user-target", "conversation-target").fingerprint == before.fingerprint
        result = await runtime.execute(identity("decision"), observation)
        expected = ResolutionKind.APPROVAL_EXPIRED if decision == "expired" else ResolutionKind.APPROVAL_DECISION
        assert result.managed.deterministic.kind is expected
        assert result.managed.checkpoint_thread_id == pending.checkpoint_thread_id
        assert result.managed.state_after.pending_approval is None
        assert store.load("tenant-target", "user-target", "conversation-target").fingerprint == result.managed.state_after.fingerprint
        assert len(workflow.items) == (1 if decision == "approved" else 0)
    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["executed", "committed"])
def test_postgres_reopen_preserves_checkpointed_pending_decision(postgres_database_url, boundary):
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
    from infrastructure.postgres_target_runtime import PostgresConversationStateStore
    PostgresMigrationRunner(postgres_database_url).upgrade()
    executor = _MissingThenReadExecutor()
    identity = IdentityFactory().create_invocation(
        tenant_id="tenant-a", user_id="user-a",
        conversation_id=f"commit-reopen-{boundary}", request_id="initial")
    async def run():
        fingerprint = None
        for phase in ("crash", "reopen"):
            pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=3))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(postgres_database_url, setup=phase == "crash") as saver:
                    manager = _manager(executor, checkpointer=saver)
                    manager._state_store = PostgresConversationStateStore(pool)
                    wrapper = CrashBoundary(manager, boundary if phase == "crash" else "")
                    runtime = TurnRuntime(wrapper, ResponseAssembler(Author(), knowledge_verifier=Verifier(True)),
                                          checkpointer=saver)
                    if phase == "crash":
                        with pytest.raises(RuntimeError, match="injected boundary"):
                            await runtime.execute(identity, TurnObservations("查询订单 DP1234"))
                        if boundary == "committed":
                            fingerprint = manager._state_store.load(identity.tenant_id, identity.user_id, identity.conversation_id).fingerprint
                    else:
                        result = await runtime.execute(identity, TurnObservations("查询订单 DP1234"))
                        assert result.managed.state_after.pending_interaction is not None
                        assert result.assembled.verified
                        if fingerprint:
                            assert result.managed.state_after.fingerprint == fingerprint
                        assert len(executor.calls) == 1
            finally:
                pool.close()
    asyncio.run(run())


@pytest.mark.parametrize("completed_node", ["prepare_turn", "execute_work_plan", "commit_turn_state"])
def test_old_inflight_prepared_checkpoint_is_explicitly_unsupported(completed_node):
    from application.turn_runtime import TurnCheckpointVersionError
    manager = _manager(_Executor())
    runtime = TurnRuntime(manager, ResponseAssembler(),
                          checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
    async def run():
        prepared = await manager.prepare(_identity(), TurnObservations("查询订单 DP1234"))
        config = {"configurable": {"thread_id": "turn:" + str(_identity().invocation_key)}}
        await runtime.graph.aupdate_state(config, {
            "invocation": _identity(), "invocation_key": str(_identity().invocation_key),
            "prepared": replace(prepared, artifact_version="prepared-turn-v1"),
            "observations": TurnObservations("查询订单 DP1234"), "execution_context": {},
        }, as_node=completed_node)
        with pytest.raises(TurnCheckpointVersionError):
            await runtime.execute(_identity(), TurnObservations("查询订单 DP1234"))
        assert manager._state_store.load("tenant-a", "user-a", "conversation-a").version == 0
    asyncio.run(run())
