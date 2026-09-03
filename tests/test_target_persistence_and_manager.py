import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
    RequestedField,
)
from application.conversation_state import (
    ConversationState,
    InMemoryConversationStateStore,
    PendingInteractionState,
    WorkstreamState,
    WorkstreamStatus,
)
from application.conversation_store import ConversationScope
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import ResolutionKind, TurnObservations
from application.orchestration_runtime import AgentContextView, OrchestrationRuntime
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
)
from application.work_item import ArgumentValue
from application.write_workflow import OperationStatus
from core.identity import IdentityFactory
from infrastructure.langgraph_checkpoint import (
    checkpoint_thread_id,
    target_checkpoint_serializer,
)
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_target_runtime import (
    PostgresConversationStateStore,
    PostgresOperationLedger,
    conversation_state_from_payload,
    conversation_state_to_payload,
    operation_from_payload,
    operation_to_payload,
)


def _identity(request_id="request-target-1"):
    return IdentityFactory(lambda: "fixed").create_invocation(
        tenant_id="tenant-target",
        user_id="user-target",
        conversation_id="conversation-target",
        request_id=request_id,
    )


class _ReadExecutor:
    async def __call__(self, context: AgentContextView):
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "VERIFIED",
            "target-test-worker-v1",
            facts=tuple(
                FactRecord(
                    f"subject:{item.work_item_id}",
                    requirement,
                    '{"value":"verified"}',
                    FactSourceKind.VERIFIED_STATE,
                    f"receipt:{item.work_item_id}:{requirement}",
                    item.allowed_tools[0],
                    "v1",
                    datetime.now(timezone.utc),
                )
                for requirement in item.requirement_ids
            ),
        )


class _Understanding:
    def __init__(self, proposal):
        self.proposal = proposal
        self.calls = []

    async def __call__(self, observations, state, deterministic, registry):
        self.calls.append((state, deterministic))
        return self.proposal


class _TerminalExecutor:
    async def __call__(self, context: AgentContextView):
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.TERMINAL_FAILURE,
            "TOOL_DISABLED_FOR_TEST",
            "target-test-worker-v1",
        )


def _order_proposal():
    return TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "order-status",
            CommandKind.DIRECT_TOOL,
            "order_logistics",
            "Query current order status",
            (ArgumentValue.create("order_id", "DP1234"),),
            ("order.current_state",),
            tool_id="order_lookup",
        ),),
        "ORDER_STATUS_UNDERSTOOD",
    )


def test_conversation_state_event_codec_round_trips_bound_pending_state():
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    ).start_workstream(WorkstreamState(
        "work-1", "product_technical", "product_identification:v1", "IDENTIFY",
        WorkstreamStatus.ACTIVE, 1,
    ))
    state = state.wait_for_interaction(PendingInteractionState(
        "interaction-1", 1,
        (RequestedField("wall_material", "work-1", "string"),),
        (),
    ))

    restored = conversation_state_from_payload(conversation_state_to_payload(state))

    assert restored == state
    assert restored.fingerprint == state.fingerprint


def test_operation_event_codec_preserves_monotonic_record():
    from application.write_workflow import OperationRecord

    record = OperationRecord(
        "operation-1", "work-item:v1:abc", OperationStatus.OUTCOME_UNKNOWN,
        4, 1, reason_code="WRITE_TRANSPORT_FAILED",
    )
    assert operation_from_payload(operation_to_payload(record)) == record


def test_multiple_workstream_starts_are_one_aggregate_transition():
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    )
    next_state = state.start_workstreams((
        WorkstreamState(
            "work-1", "billing_refund", "execute_refund:v1", "CHECK",
            WorkstreamStatus.ACTIVE, 1, flow_ref="execute_refund:v1",
        ),
        WorkstreamState(
            "work-2", "human_service", "human_handoff:v1", "PREPARE",
            WorkstreamStatus.ACTIVE, 1, flow_ref="human_handoff:v1",
        ),
    ))
    assert next_state.version == 1
    assert {item.workstream_id for item in next_state.workstreams} == {"work-1", "work-2"}


def test_manager_uses_direct_path_and_checkpoint_thread_without_agent_fanout():
    identity = _identity()
    store = InMemoryConversationStateStore()
    understanding = _Understanding(_order_proposal())
    checkpointer = InMemorySaver(serde=target_checkpoint_serializer())
    executor = _ReadExecutor()
    runtime = OrchestrationRuntime(
        direct_executor=executor,
        domain_workers={},
        checkpointer=checkpointer,
    )
    manager = TargetConversationManager(
        state_store=store,
        registry=build_default_capability_registry("tenant-target"),
        understanding=understanding,
        orchestration=runtime,
    )

    result = asyncio.run(manager.handle(identity, TurnObservations("DP1234 到哪了")))

    assert result.plan.route.mode.value == "DIRECT"
    assert result.board.complete
    assert result.checkpoint_thread_id == str(identity.invocation_key)
    snapshot = runtime.graph.get_state({
        "configurable": {"thread_id": str(identity.invocation_key)},
    })
    assert snapshot.values["board"].complete

    replay = asyncio.run(manager.handle(identity, TurnObservations("DP1234 到哪了")))
    assert replay.board == result.board


def test_manager_consumes_pending_input_before_understanding_and_persists_it():
    identity = _identity("request-target-2")
    store = InMemoryConversationStateStore()
    initial = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
    started = initial.start_workstream(WorkstreamState(
        "work-1", "order_logistics", "order_lookup:v1", "WAIT_ORDER_ID",
        WorkstreamStatus.ACTIVE, 1,
    ))
    assert store.compare_and_set(initial, started)
    waiting = started.wait_for_interaction(PendingInteractionState(
        "interaction-1", 1,
        (RequestedField("order_id", "work-1", "string"),),
        (),
    ))
    assert store.compare_and_set(started, waiting)
    understanding = _Understanding(_order_proposal())
    manager = TargetConversationManager(
        state_store=store,
        registry=build_default_capability_registry("tenant-target"),
        understanding=understanding,
        orchestration=OrchestrationRuntime(
            direct_executor=_ReadExecutor(), domain_workers={},
        ),
    )

    result = asyncio.run(manager.handle(identity, TurnObservations("DP1234")))

    observed_state, deterministic = understanding.calls[0]
    assert deterministic.kind is ResolutionKind.FILL_PENDING_INPUT
    assert observed_state.pending_interaction is None
    assert dict((item.name, item.value) for item in observed_state.workstreams[0].slots) == {
        "order_id": "DP1234",
    }
    assert result.state_after == observed_state


def test_manager_commits_workflow_start_before_dispatch():
    identity = _identity("request-target-workflow")
    store = InMemoryConversationStateStore()
    proposal = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "refund",
            CommandKind.START_WORKFLOW,
            "billing_refund",
            "Create refund",
            (ArgumentValue.create("order_id", "DP1234"),),
            ("refund.request_action",),
            flow_ref="execute_refund:v1",
            action_ref="refund.request.create:v1",
            target_entity_ref="order:DP1234",
            target_entity_version="order:DP1234:v1",
            approval_binding="approval-1",
        ),),
        "REFUND_REQUEST_UNDERSTOOD",
    )
    manager = TargetConversationManager(
        state_store=store,
        registry=build_default_capability_registry("tenant-target"),
        understanding=_Understanding(proposal),
        orchestration=OrchestrationRuntime(
            direct_executor=_TerminalExecutor(),
            domain_workers={"billing_refund": _TerminalExecutor()},
        ),
    )

    result = asyncio.run(manager.handle(identity, TurnObservations("退款")))

    assert result.state_after.version == 1
    assert len(result.state_after.active_workstreams) == 1
    workstream = result.state_after.active_workstreams[0]
    assert workstream.flow_ref == "execute_refund:v1"
    assert workstream.phase == "CHECK_ELIGIBILITY"
    persisted = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
    assert persisted == result.state_after


def test_checkpoint_thread_id_rejects_blank_or_oversized_values():
    with pytest.raises(ValueError, match="required"):
        checkpoint_thread_id("")
    with pytest.raises(ValueError, match="exceeds"):
        checkpoint_thread_id("x" * 256)


def test_postgres_target_state_and_operation_ledgers_are_replayable(
    postgres_database_url,
):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    pool.open()
    suffix = uuid4().hex
    identity = IdentityFactory(lambda: suffix).create_invocation(
        tenant_id=f"tenant-{suffix}",
        user_id=f"user-{suffix}",
        conversation_id=f"conversation-{suffix}",
        request_id=f"request-{suffix}",
    )
    try:
        state_store = PostgresConversationStateStore(pool)
        current = state_store.load(
            identity.tenant_id, identity.user_id, identity.conversation_id,
        )
        next_state = current.start_workstream(WorkstreamState(
            "work-1", "order_logistics", "order_lookup:v1", "QUERY",
            WorkstreamStatus.ACTIVE, 1,
        ))
        assert state_store.compare_and_set(current, next_state)
        assert not state_store.compare_and_set(current, next_state)
        assert state_store.load(
            identity.tenant_id, identity.user_id, identity.conversation_id,
        ) == next_state

        registry = build_default_capability_registry(str(identity.tenant_id))
        from application.turn_planning import RoutePolicy, TurnPlanCompiler
        validated = RoutePolicy().accept(
            TurnProposal(
                ProposalDisposition.RESOLVED,
                (CommandProposal(
                    "refund",
                    CommandKind.START_WORKFLOW,
                    "billing_refund",
                    "Create refund",
                    (ArgumentValue.create("order_id", "DP1234"),),
                    ("refund.request_action",),
                    flow_ref="execute_refund:v1",
                    action_ref="refund.request.create:v1",
                    target_entity_ref="order:DP1234",
                    target_entity_version="order:DP1234:v1",
                    approval_binding="approval-1",
                ),),
                "REFUND",
            ),
            next_state,
            registry,
        )
        item = TurnPlanCompiler().compile(
            validated, next_state, registry, identity,
        ).work.items[0]
        ledger = PostgresOperationLedger(pool, ConversationScope(
            identity.tenant_id, identity.user_id, identity.conversation_id,
        ))
        operation = ledger.acquire(item)
        executing = replace(
            operation,
            status=OperationStatus.EXECUTING,
            version=operation.version + 1,
            attempts=1,
            reason_code="STARTED",
        )
        assert ledger.compare_and_set(operation, executing)
        assert not ledger.compare_and_set(operation, executing)
        assert ledger.acquire(item) == executing
    finally:
        pool.close()
