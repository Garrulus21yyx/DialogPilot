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
from tests.test_knowledge_answer_boundary import Verifier
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
            json.dumps({"order_id": order_id, "status": "shipped"}, separators=(",", ":")),
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


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("with_input", [False, True])
def test_conversation_only_reply_retries_only_undelivered_proposal(published, with_input):
    from types import SimpleNamespace
    from application.target_conversation_manager import TargetTurnContext
    from application.conversation_state import PendingApprovalState
    from application.agent_result import MissingInputSpec
    from application.response_assembly import AssembledResponse, ResponseAssemblyMode
    from tests.test_approval_conversation import domain, call
    from langchain_core.messages import AIMessage
    agent, context, _, _ = domain([call("prepare_order_cancel"), AIMessage(content="Pending.")])
    action = asyncio.run(agent(context)).pending_action
    pending = PendingApprovalState(action.approval_binding, 1, "flow", action.work_item_id,
        action.action_ref, action.operation_key, action.aggregate_ref, action.target_entity_version,
        "2099-01-01T00:00:00+00:00", arguments=action.arguments)
    calls = []
    class Assembler:
        async def assemble(self, *args, **kwargs):
            calls.append(kwargs)
            return AssembledResponse("The proposal has not executed.", ResponseAssemblyMode.TEMPLATE,
                                     (), False, "NOT_REQUIRED", "TEST")
    runtime = TurnRuntime(SimpleNamespace(), Assembler(), interaction_published=lambda *args, **kwargs: published)
    field = SimpleNamespace(field_name="color", target_work_item_id="other",
        value_schema="string", question_hint="Which color?")
    pending_input = SimpleNamespace(interaction_id="input", version=1,
        requested_fields=(field,)) if with_input else None
    after = SimpleNamespace(pending_approval=pending, pending_interaction=pending_input)
    managed = SimpleNamespace(board=None, state_after=after, request_completed=False, diagnostics=(),
                              interaction_questions=(MissingInputSpec("color", "other", "INPUT", "string", "Which color?"),),
                              plan=SimpleNamespace(response_text="What would the cancellation affect?",
                                  route=SimpleNamespace(reason_code="TEST", missing_inputs=())))
    asyncio.run(runtime._assemble_response({"managed": managed,
        "prepared": SimpleNamespace(context=TargetTurnContext()),
        "presentation_state": after, "observations": TurnObservations("What would it affect?"),
        "invocation": _identity()}))
    if published:
        supplied = calls[0]["conversation_context"]["retained_approval"]
        assert supplied["operations"] == [op.view() for op in pending.operations]
        assert supplied["status"] == "AWAITING_DECISION_NOT_EXECUTED"
        assert "pending_approval" not in calls[0]
    else:
        assert calls[0]["pending_approval"] is pending
        assert calls[0]["response_candidate"] is None
        assert "retained_approval" not in calls[0]["conversation_context"]
        assert len(calls[0]["requested_inputs"]) == int(with_input)
    assert after.pending_approval is pending


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

    async def commit(self, result):
        await self.manager.commit(result)

    async def commit_progress(self, result):
        await self.manager.commit_progress(result)

    async def resolve_followup(self, prepared, result):
        return await self.manager.resolve_followup(prepared, result)


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
        self.delegate = ResponseAssembler(knowledge_verifier=Verifier(True))

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


def test_question_bypasses_unavailable_author_and_replays_bound_response_without_work():
    from tests.test_target_chat_cutover import _MissingThenReadExecutor
    executor = _MissingThenReadExecutor()
    checkpoint = InMemorySaver(serde=target_checkpoint_serializer())
    manager = _CountingManager(_manager(executor, checkpointer=checkpoint))
    class Composer:
        calls = 0
        async def compose(self, payload):
            self.calls += 1
            if self.calls == 1:
                from core.framework_models import ModelInvocationError
                raise ModelInvocationError('compose', TimeoutError('temporary model failure'))
            return "\n".join([('请提供订单核验信息。')])
    composer = Composer()
    runtime = TurnRuntime(manager, ResponseAssembler(composer, knowledge_verifier=Verifier(True)),
                          checkpointer=checkpoint)
    async def run():
        first = await runtime.execute(_identity(), TurnObservations('查询订单 DP1234'))
        assert not first.assembled.verified
        assert first.assembled.text
        saved = await runtime.graph.aget_state({'configurable': {'thread_id': 'turn:' + str(_identity().invocation_key)}})
        pending = saved.values['managed'].state_after.pending_interaction
        assert pending is not None
        result = await runtime.execute(_identity(), TurnObservations('查询订单 DP1234'))
        assert result.managed.state_after.pending_interaction == pending
        assert result.assembled == first.assembled
    asyncio.run(run())
    assert manager.prepare_calls == manager.execute_calls == 1
    assert len(executor.calls) == 1
    assert composer.calls == 0


def test_question_does_not_invoke_or_retry_a_rejecting_reviewer():
    from tests.test_target_chat_cutover import _MissingThenReadExecutor
    checkpoint = InMemorySaver(serde=target_checkpoint_serializer())
    class Composer:
        calls = 0
        async def compose(self, payload):
            self.calls += 1
            return "\n".join([('Unsupported question premise.')])
    composer = Composer()
    runtime = TurnRuntime(_manager(_MissingThenReadExecutor()),
        ResponseAssembler(composer, knowledge_verifier=Verifier(False)), checkpointer=checkpoint)
    result = asyncio.run(runtime.execute(_identity(), TurnObservations('查询订单 DP1234')))
    assert result.managed.state_after.pending_interaction is not None
    assert not result.assembled.verified
    assert not result.assembled.retryable
    assert result.assembled.interaction_ready
    assert result.assembled.verification_reason == 'BOUND_QUESTION_NO_MODEL_REVIEW'
    assert result.assembled.diagnostics == ()
    assert composer.calls == 0


@pytest.mark.parametrize('optional_count', [0, 1, 3])
def test_question_presentation_uses_exact_persisted_bindings_not_optional_hints(optional_count):
    from dataclasses import replace
    from application.agent_result import MissingInputSpec
    from tests.test_target_chat_cutover import _MissingThenReadExecutor
    class Executor(_MissingThenReadExecutor):
        async def __call__(self, context):
            result = await super().__call__(context)
            return replace(result, missing_inputs=(*result.missing_inputs, *(
                MissingInputSpec(f'optional{i}', result.work_item_id, 'OPTIONAL', 'string',
                                 'Optional information', required=False) for i in range(optional_count))))
    class Composer:
        payload = None
        async def compose(self, payload):
            self.payload = payload
            return "\n".join([('请提供订单核验信息。')])
    composer = Composer()
    runtime = TurnRuntime(_manager(Executor()), ResponseAssembler(composer, knowledge_verifier=Verifier(True)))
    result = asyncio.run(runtime.execute(_identity(), TurnObservations('查询订单 DP1234')))
    fields = result.managed.state_after.pending_interaction.requested_fields
    assert composer.payload is None
    assert len(fields) == 1 and result.assembled.interaction_ready
    assert fields[0].question_hint in result.assembled.text
    assert 'Optional information' not in result.assembled.text


def test_checkpoint_allowlist_is_closed_over_registered_dataclass_field_types():
    """Nested state must decode too, not just its top-level dataclass."""
    from dataclasses import is_dataclass
    from enum import Enum
    from importlib import import_module
    from typing import get_args, get_type_hints
    from infrastructure.langgraph_checkpoint import _TARGET_CHECKPOINT_TYPES
    allowed = set(_TARGET_CHECKPOINT_TYPES)
    missing = set()
    def inspect(annotation):
        if isinstance(annotation, type) and (is_dataclass(annotation) or issubclass(annotation, Enum)):
            identity = (annotation.__module__, annotation.__name__)
            if annotation.__module__.startswith(('application.', 'core.')) and identity not in allowed:
                missing.add(identity)
        for argument in get_args(annotation):
            inspect(argument)
    for module, name in allowed:
        cls = getattr(import_module(module), name)
        if is_dataclass(cls):
            for annotation in get_type_hints(cls).values():
                inspect(annotation)
    assert not missing, f'Nested checkpoint types are not registered: {sorted(missing)}'


def test_bound_question_survives_postgres_reopen_without_author_call(postgres_database_url):
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    from tests.test_target_chat_cutover import _MissingThenReadExecutor
    from uuid import uuid4
    identity = IdentityFactory().create_invocation(tenant_id='tenant-a', user_id='user-a',
        conversation_id='question-' + uuid4().hex, request_id='request')
    executor = _MissingThenReadExecutor()
    manager = _CountingManager(_manager(executor))
    class Composer:
        calls = 0
        async def compose(self, payload):
            self.calls += 1
            if self.calls == 1:
                from core.framework_models import ModelInvocationError
                raise ModelInvocationError('compose', TimeoutError('injected provider outage'))
            return "\n".join([('请提供订单核验信息。')])
    composer = Composer()
    async def run():
        async with AsyncPostgresCheckpointOwner(postgres_database_url, setup=True) as checkpoint:
            runtime = TurnRuntime(manager, ResponseAssembler(composer, knowledge_verifier=Verifier(True)),
                                  checkpointer=checkpoint)
            first = await runtime.execute(identity, TurnObservations('查询订单 DP1234'))
            assert first.assembled.interaction_ready and not first.assembled.verified
            assert not first.assembled.retryable
            snapshot = await runtime.graph.aget_state({'configurable': {'thread_id': 'turn:' + str(identity.invocation_key)}})
            pending = snapshot.values['managed'].state_after.pending_interaction
        async with AsyncPostgresCheckpointOwner(postgres_database_url) as checkpoint:
            runtime = TurnRuntime(manager, ResponseAssembler(composer, knowledge_verifier=Verifier(True)),
                                  checkpointer=checkpoint)
            result = await runtime.execute(identity, TurnObservations('查询订单 DP1234'))
            assert result.managed.state_after.pending_interaction == pending
            assert result.assembled == first.assembled
    asyncio.run(run())
    assert manager.prepare_calls == manager.execute_calls == len(executor.calls) == 1
    assert composer.calls == 0


@pytest.mark.parametrize("old_version", ["turn-runtime-v7-result-owned-delivery", "turn-runtime-v11-observation-progress", "turn-runtime-v12-policy-evidence", "turn-runtime-v13-action-semantics", "turn-runtime-v17-assignment-repair", "turn-runtime-v18-implicit-current-action", "turn-runtime-v19-explicit-public-response", "turn-runtime-v23-worker-recovery"])
def test_unpublished_prior_lifecycle_checkpoint_is_not_reinterpreted_as_new_execution(old_version):
    from application.turn_runtime import TurnCheckpointVersionError
    executor = _Executor()
    runtime = TurnRuntime(_manager(executor), ResponseAssembler(),
        checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
    async def run():
        identity = _identity()
        await runtime.execute(identity, TurnObservations("查询订单 DP1234"))
        calls = executor.calls
        config = {"configurable": {"thread_id": "turn:" + str(identity.invocation_key)}}
        await runtime.graph.aupdate_state(config, {"runtime_version": old_version})
        with pytest.raises(TurnCheckpointVersionError, match="explicit lifecycle migration"):
            await runtime.execute(identity, TurnObservations("查询订单 DP1234"))
        assert executor.calls == calls
    asyncio.run(run())


def test_turn_graph_retries_failed_prepare_without_executing_work_early():
    executor = _Executor()
    manager = _FailOncePrepareManager(_manager(executor))
    runtime = TurnRuntime(
        manager,
        ResponseAssembler(knowledge_verifier=Verifier(True)),
        checkpointer=InMemorySaver(serde=target_checkpoint_serializer()),
    )

    with pytest.raises(RuntimeError, match="injected prepare crash"):
        asyncio.run(runtime.execute(
            _identity(), TurnObservations("查询订单 DP1234"),
        ))

    result = asyncio.run(runtime.execute(
        _identity(), TurnObservations("查询订单 DP1234"),
    ))
    observed = result.managed.board.facts[0].observed_at.isoformat()
    assert result.assembled.text == f"在 {observed} 的查询记录中，订单 DP1234 状态为已发货。"
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
        ResponseAssembler(knowledge_verifier=Verifier(True)),
        checkpointer=checkpointer,
    )

    with pytest.raises(RuntimeError, match="injected post-execution crash"):
        asyncio.run(runtime.execute(
            _identity(), TurnObservations("查询订单 DP1234"),
        ))

    result = asyncio.run(runtime.execute(
        _identity(), TurnObservations("查询订单 DP1234"),
    ))
    observed = result.managed.board.facts[0].observed_at.isoformat()
    assert result.assembled.text == f"在 {observed} 的查询记录中，订单 DP1234 状态为已发货。"
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

    observed = result.managed.board.facts[0].observed_at.isoformat()
    assert result.assembled.text == f"在 {observed} 的查询记录中，订单 DP1234 状态为已发货。"
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
    runtime = TurnRuntime(_manager(Executor()), ResponseAssembler(knowledge_verifier=Verifier(True)),
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
    assert assembler.contexts == [{**conversation_context_payload(context), "request_completed": True,
        "turn_execution": {"phase": "REPLY", "continues_after_reply": False,
                           "waiting_for_input": False, "waiting_for_approval": False}}] * 2


@pytest.mark.parametrize("legacy_fields", [
    {"rejected_input_work_items": [], "interaction_feedback": ""},
    {"rejected_input_work_items": ["work"], "interaction_feedback": "Retry domain"},
])
def test_old_reply_checkpoint_is_explicitly_rejected_not_silently_lost(legacy_fields):
    import ormsgpack
    from langgraph.checkpoint.serde.jsonplus import EXT_CONSTRUCTOR_KW_ARGS
    from infrastructure.langgraph_checkpoint import TargetCheckpointContractError
    fields = {"text": "Saved reply", "mode": "template", "evidence_refs": [],
        "composer_used": False, "verification_status": "NOT_CHECKED",
        "verification_reason": "legacy", **legacy_fields}
    old = ormsgpack.Ext(EXT_CONSTRUCTOR_KW_ARGS, ormsgpack.packb(
        ("application.response_assembly", "AssembledResponse", fields)))
    payload = ("msgpack", ormsgpack.packb({"assembled": old}))
    with pytest.raises(TargetCheckpointContractError, match="legacy response checkpoint"):
        target_checkpoint_serializer().loads_typed(payload)
