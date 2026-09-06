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
    MissingInputSpec,
    ReceiptRef,
    RequestedField,
)
from application.capability_registry import ActionPreparationDefinition
from application.conversation_state import (
    ConversationState,
    InMemoryConversationStateStore,
    PendingInteractionState,
    PendingApprovalState,
    WorkstreamState,
    WorkstreamStatus,
)
from application.conversation_store import ConversationScope
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import ResolutionKind, TurnObservations
from application.orchestration_runtime import AgentContextView, OrchestrationRuntime
from application.target_conversation_manager import TargetConversationManager
from application.target_understanding import StateBoundTargetUnderstanding
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

    async def __call__(
        self, observations, state, deterministic, registry, turn_context=None,
    ):
        self.calls.append((state, deterministic))
        return self.proposal


class _ResumeAwareUnderstanding:
    def __init__(self, initial):
        self.initial = initial
        self.state_bound = StateBoundTargetUnderstanding()

    async def __call__(
        self, observations, state, deterministic, registry, turn_context=None,
    ):
        if deterministic.kind is not ResolutionKind.UNRESOLVED:
            resolved = await self.state_bound(
                observations, state, deterministic, registry, turn_context,
            )
            if resolved is not None:
                return resolved
        return self.initial


class _ProductReferenceWorker:
    def __init__(self):
        self.calls = []

    async def __call__(self, context: AgentContextView):
        item = context.work_item
        arguments = {value.name: value.value for value in item.arguments}
        self.calls.append(arguments)
        if "product_reference" not in arguments:
            return AgentResult(
                item.work_item_id,
                item.owner_agent,
                AgentResultStatus.NEEDS_USER_INPUT,
                "PRODUCT_REFERENCE_REQUIRED",
                "product-worker-test-v1",
                missing_inputs=(MissingInputSpec(
                    "product_reference",
                    item.work_item_id,
                    "PRODUCT_REFERENCE_REQUIRED",
                    "string",
                    "请提供商品链接、SKU 或商品名称。",
                ),),
            )
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "PRODUCT_ANSWER_FOUND",
            "product-worker-test-v1",
            facts=(FactRecord(
                f"product:{arguments['product_reference']}",
                "knowledge.active_source",
                '{"answer":"supported"}',
                FactSourceKind.KNOWLEDGE_ASSERTED,
                "knowledge:product-answer",
                "knowledge_search",
                "knowledge-v1",
                datetime.now(timezone.utc),
            ),),
            candidate_response="已根据该商品的目录与知识证据回答。",
        )


class _OrderVerificationWorker:
    async def __call__(self, context: AgentContextView):
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.NEEDS_USER_INPUT,
            "ORDER_VERIFICATION_REQUIRED",
            "order-worker-test-v1",
            missing_inputs=(MissingInputSpec(
                "verification_reference",
                item.work_item_id,
                "ORDER_VERIFICATION_REQUIRED",
                "string",
                "请提供订单核验信息。",
            ),),
        )


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


class _EligibilityExecutor:
    def __init__(self, eligible: bool):
        self.eligible = eligible

    async def __call__(self, context: AgentContextView):
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "ELIGIBILITY_READ",
            "eligibility-test-v1",
            facts=(FactRecord(
                "order:DP1234",
                "refund.eligibility",
                (
                    '{"eligible":true,"order_version":7}'
                    if self.eligible
                    else '{"eligible":false,"order_version":7}'
                ),
                FactSourceKind.VERIFIED_STATE,
                "eligibility-receipt",
                "refund_eligibility_check",
                "refund-eligibility-v2",
                datetime.now(timezone.utc),
            ),),
        )


class _RegistryDrivenPreparationExecutor:
    async def __call__(self, context: AgentContextView):
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "PREPARATION_READ",
            "registry-preparation-test-v1",
            facts=(FactRecord(
                "order:DP1234",
                "refund.eligibility",
                '{"permitted":"yes","revision_token":"revision-7"}',
                FactSourceKind.VERIFIED_STATE,
                "preparation-receipt",
                "refund_eligibility_check",
                "generic-preparation-v1",
                datetime.now(timezone.utc),
            ),),
        )


class _OrderCancellationPreparationExecutor:
    async def __call__(self, context: AgentContextView):
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "ORDER_STATE_READ",
            "order-preparation-test-v1",
            facts=(FactRecord(
                "order:DP1234",
                "order.current_state",
                '{"order_id":"DP1234","status":"paid","version":4}',
                FactSourceKind.VERIFIED_STATE,
                "order-state-receipt",
                "order_lookup",
                "order-view-v2",
                datetime.now(timezone.utc),
            ),),
        )


class _OrderCancellationWorkflowExecutor:
    def __init__(self):
        self.items = []

    async def __call__(self, context: AgentContextView):
        item = context.work_item
        self.items.append(item)
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "TOOL_COMMITTED",
            "order-cancel-test-v1",
            action_receipts=(ReceiptRef(
                "cancellation-1",
                "action-receipt-v1",
                str(item.operation_key),
                "COMMITTED",
                "order.cancel_action",
            ),),
        )


class _AddressChangeWorkflowExecutor:
    def __init__(self):
        self.items = []

    async def __call__(self, context: AgentContextView):
        item = context.work_item
        self.items.append(item)
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "TOOL_COMMITTED",
            "address-change-test-v1",
            action_receipts=(ReceiptRef(
                "address-change-1",
                "action-receipt-v1",
                str(item.operation_key),
                "COMMITTED",
                "order.shipping_address_action",
            ),),
        )


class _AccountFreezePreparationExecutor:
    async def __call__(self, context: AgentContextView):
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "ACCOUNT_STATE_READ",
            "account-state-test-v1",
            facts=(FactRecord(
                "account:user-target",
                "account.current_state",
                '{"status":"active","updated_at":"2026-09-03T00:00:00+00:00","version":5}',
                FactSourceKind.VERIFIED_STATE,
                "account-state-receipt",
                "account_security_state",
                "account-security-state-v1",
                datetime.now(timezone.utc),
            ),),
        )


class _AccountFreezeWorkflowExecutor:
    def __init__(self):
        self.items = []

    async def __call__(self, context: AgentContextView):
        item = context.work_item
        self.items.append(item)
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "TOOL_COMMITTED",
            "account-freeze-test-v1",
            action_receipts=(ReceiptRef(
                "freeze-1",
                "action-receipt-v1",
                str(item.operation_key),
                "COMMITTED",
                "account.freeze_action",
            ),),
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


def _prepare_workflow_proposal(
    *, command_id, owner, objective, arguments, requirement_id,
    flow_ref, action_ref, target_entity_ref,
):
    return TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            command_id,
            CommandKind.PREPARE_ACTION,
            owner,
            objective,
            tuple(ArgumentValue.create(name, value) for name, value in arguments),
            (requirement_id,),
            flow_ref=flow_ref,
            action_ref=action_ref,
            target_entity_ref=target_entity_ref,
        ),),
        "TEST_WORKFLOW_PREPARATION",
    )


def _refund_preparation(reason="把订单 DP1234 退款"):
    return _prepare_workflow_proposal(
        command_id="prepare-refund",
        owner="billing_refund",
        objective="Check refund eligibility before a governed write",
        arguments=(("order_id", "DP1234"), ("reason", reason)),
        requirement_id="refund.eligibility",
        flow_ref="execute_refund:v1",
        action_ref="refund.request.create:v1",
        target_entity_ref="order:DP1234",
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
        (RequestedField("product_reference", "work-1", "string"),),
        (),
    ))

    restored = conversation_state_from_payload(conversation_state_to_payload(state))

    assert restored == state
    assert restored.fingerprint == state.fingerprint


def test_conversation_state_codec_preserves_approval_execution_bindings():
    state = ConversationState.empty(
        tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a",
    ).start_workstream(WorkstreamState(
        "refund-ws", "billing_refund", "execute_refund:v1", "CHECK",
        WorkstreamStatus.ACTIVE, 1, flow_ref="execute_refund:v1",
    ))
    state = state.wait_for_approval(PendingApprovalState(
        "approval-1", 1, "refund-ws", "prepare-work",
        "refund.request.create:v1", "operation-1", "order:DP1234", "7",
        "2099-01-01T00:00:00+00:00",
        (
            ArgumentValue.create("order_id", "DP1234"),
            ArgumentValue.create("expected_order_version", 7),
        ),
    ))

    restored = conversation_state_from_payload(conversation_state_to_payload(state))

    assert restored == state
    assert restored.pending_approval.operation_key == "operation-1"
    assert restored.pending_approval.target_entity_version == "7"
    for changes in ({"operation_key": "another-operation"},
                    {"target_entity_version": "8"},
                    {"action_ref": "another-action:v1"},
                    {"arguments": (ArgumentValue.create("order_id", "DP9999"),)}):
        changed = replace(restored, pending_approval=replace(restored.pending_approval, **changes))
        assert changed.fingerprint != restored.fingerprint

    accepted = restored.consume_approval(
        approval_id="approval-1", approval_version=1, approved=True,
    )
    accepted_restored = conversation_state_from_payload(
        conversation_state_to_payload(accepted)
    )
    assert accepted_restored == accepted
    assert accepted_restored.accepted_approvals[0].operation_key == "operation-1"


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


@pytest.mark.parametrize(("eligible", "expected_status"), [
    (True, WorkstreamStatus.WAITING_APPROVAL),
    (False, WorkstreamStatus.CANCELLED),
])
def test_refund_preparation_derives_control_state_from_authoritative_eligibility(
    eligible,
    expected_status,
):
    identity = _identity(f"request-eligibility-{eligible}")
    store = InMemoryConversationStateStore()
    manager = TargetConversationManager(
        state_store=store,
        registry=build_default_capability_registry("tenant-target"),
        understanding=_ResumeAwareUnderstanding(_refund_preparation()),
        orchestration=OrchestrationRuntime(
            direct_executor=_EligibilityExecutor(eligible), domain_workers={},
        ),
    )

    result = asyncio.run(manager.handle(
        identity, TurnObservations("把订单 DP1234 退款"),
    ))

    assert result.state_after.workstreams[0].status is expected_status
    assert (result.state_after.pending_approval is not None) is eligible
    if eligible:
        pending = result.state_after.pending_approval
        assert pending.target_entity_version == "7"
        assert dict((item.name, item.value) for item in pending.arguments) == {
            "order_id": "DP1234",
            "reason": "把订单 DP1234 退款",
            "expected_order_version": 7,
        }


def test_manager_interprets_workflow_preparation_only_from_registry_bindings():
    identity = _identity("request-registry-preparation")
    store = InMemoryConversationStateStore()
    base = build_default_capability_registry("tenant-target")
    action = replace(
        base.actions[0],
        preparation=ActionPreparationDefinition.create(
            tool_id="refund_eligibility_check",
            requirement_id="refund.eligibility",
            readiness_field="permitted",
            readiness_value="yes",
            target_version_field="revision_token",
            target_version_argument="expected_revision",
        ),
    )
    registry = replace(base, actions=(action, *base.actions[1:]))
    manager = TargetConversationManager(
        state_store=store,
        registry=registry,
        understanding=_ResumeAwareUnderstanding(_refund_preparation()),
        orchestration=OrchestrationRuntime(
            direct_executor=_RegistryDrivenPreparationExecutor(),
            domain_workers={},
        ),
    )

    result = asyncio.run(manager.handle(
        identity, TurnObservations("把订单 DP1234 退款"),
    ))

    pending = result.state_after.pending_approval
    assert pending is not None
    assert pending.target_entity_version == "revision-7"
    assert dict((item.name, item.value) for item in pending.arguments) == {
        "order_id": "DP1234",
        "reason": "把订单 DP1234 退款",
        "expected_revision": "revision-7",
    }


@pytest.mark.parametrize("flow_ref", ["cancel_order:v1", None])
def test_order_cancellation_approval_resumes_its_registered_owner_and_action(flow_ref):
    store = InMemoryConversationStateStore()
    registry = build_default_capability_registry("tenant-target")
    registry = replace(registry, actions=tuple(
        replace(action, flow_ref=flow_ref) if action.action_id == "order.cancel" else action
        for action in registry.actions
    ))
    workflow = _OrderCancellationWorkflowExecutor()
    checkpointer = InMemorySaver(serde=target_checkpoint_serializer())
    manager = TargetConversationManager(
        state_store=store,
        registry=registry,
        understanding=_ResumeAwareUnderstanding(_prepare_workflow_proposal(
            command_id="prepare-order-cancellation",
            owner="order_logistics",
            objective="Check current order state before cancellation",
            arguments=(("order_id", "DP1234"),),
            requirement_id="order.current_state",
            flow_ref=flow_ref,
            action_ref="order.cancel:v1",
            target_entity_ref="order:DP1234",
        )),
        orchestration=OrchestrationRuntime(
            direct_executor=_OrderCancellationPreparationExecutor(),
            domain_workers={},
            workflow_executor=workflow,
            checkpointer=checkpointer,
        ),
    )

    prepared = asyncio.run(manager.handle(
        _identity("request-cancel-prepare"),
        TurnObservations("取消订单 DP1234"),
    ))
    pending = prepared.state_after.pending_approval
    assert pending is not None
    assert pending.action_ref == "order.cancel:v1"
    assert pending.target_entity_version == "4"
    assert pending.checkpoint_thread_id == prepared.checkpoint_thread_id

    completed = asyncio.run(manager.handle(
        _identity("request-cancel-confirm"),
        TurnObservations(
            "确认取消",
            approval_decision=True,
            approval_id=pending.approval_id,
        ),
    ))

    assert completed.state_after.workstreams[0].status is WorkstreamStatus.COMPLETED
    assert completed.checkpoint_thread_id == prepared.checkpoint_thread_id
    executed = workflow.items[0]
    assert executed.owner_agent == "order_logistics"
    assert executed.action_ref == "order.cancel:v1"
    assert executed.flow_ref == flow_ref
    assert executed.control_mode.value == ("WORKFLOW" if flow_ref else "ACTION")
    assert set(executed.allowed_tools) == {"order_cancel", "order_cancel_status"}
    assert dict((item.name, item.value) for item in executed.arguments) == {
        "order_id": "DP1234",
        "expected_order_version": 4,
    }


def test_shipping_address_approval_resumes_registered_action_with_exact_address():
    store = InMemoryConversationStateStore()
    registry = build_default_capability_registry("tenant-target")
    workflow = _AddressChangeWorkflowExecutor()
    manager = TargetConversationManager(
        state_store=store,
        registry=registry,
        understanding=_ResumeAwareUnderstanding(_prepare_workflow_proposal(
            command_id="prepare-address-change",
            owner="order_logistics",
            objective="Check current order state before changing address",
            arguments=(
                ("order_id", "DP1234"),
                ("new_address", "Berlin Example Street 9"),
            ),
            requirement_id="order.current_state",
            flow_ref="change_shipping_address:v1",
            action_ref="order.shipping_address.change:v1",
            target_entity_ref="order:DP1234",
        )),
        orchestration=OrchestrationRuntime(
            direct_executor=_OrderCancellationPreparationExecutor(),
            domain_workers={},
            workflow_executor=workflow,
        ),
    )

    prepared = asyncio.run(manager.handle(
        _identity("request-address-prepare"),
        TurnObservations(
            "把订单 DP1234 的地址改成 Berlin Example Street 9",
        ),
    ))
    pending = prepared.state_after.pending_approval
    assert pending is not None
    assert pending.action_ref == "order.shipping_address.change:v1"
    assert pending.target_entity_version == "4"

    completed = asyncio.run(manager.handle(
        _identity("request-address-confirm"),
        TurnObservations(
            "确认修改",
            approval_decision=True,
            approval_id=pending.approval_id,
        ),
    ))

    assert completed.state_after.workstreams[0].status is WorkstreamStatus.COMPLETED
    executed = workflow.items[0]
    assert executed.owner_agent == "order_logistics"
    assert executed.action_ref == "order.shipping_address.change:v1"
    assert executed.flow_ref == "change_shipping_address:v1"
    assert set(executed.allowed_tools) == {
        "shipping_address_change", "shipping_address_change_status",
    }
    assert dict((item.name, item.value) for item in executed.arguments) == {
        "order_id": "DP1234",
        "new_address": "Berlin Example Street 9",
        "expected_order_version": 4,
    }


def test_account_freeze_approval_resumes_registered_action_for_current_principal():
    store = InMemoryConversationStateStore()
    registry = build_default_capability_registry("tenant-target")
    workflow = _AccountFreezeWorkflowExecutor()
    manager = TargetConversationManager(
        state_store=store,
        registry=registry,
        understanding=_ResumeAwareUnderstanding(_prepare_workflow_proposal(
            command_id="prepare-account-freeze",
            owner="account_security",
            objective="Check current account state before freezing",
            arguments=(),
            requirement_id="account.current_state",
            flow_ref="freeze_account:v1",
            action_ref="account.freeze:v1",
            target_entity_ref="account:user-target",
        )),
        orchestration=OrchestrationRuntime(
            direct_executor=_AccountFreezePreparationExecutor(),
            domain_workers={},
            workflow_executor=workflow,
        ),
    )

    prepared = asyncio.run(manager.handle(
        _identity("request-freeze-prepare"),
        TurnObservations("立即冻结账户"),
    ))
    pending = prepared.state_after.pending_approval
    assert pending is not None
    assert pending.action_ref == "account.freeze:v1"
    assert pending.target_entity_ref == "account:user-target"
    assert pending.target_entity_version == "5"

    completed = asyncio.run(manager.handle(
        _identity("request-freeze-confirm"),
        TurnObservations(
            "确认冻结",
            approval_decision=True,
            approval_id=pending.approval_id,
        ),
    ))

    assert completed.state_after.workstreams[0].status is WorkstreamStatus.COMPLETED
    executed = workflow.items[0]
    assert executed.owner_agent == "account_security"
    assert executed.action_ref == "account.freeze:v1"
    assert executed.flow_ref == "freeze_account:v1"
    assert set(executed.allowed_tools) == {
        "account_freeze", "account_freeze_status",
    }
    assert dict((item.name, item.value) for item in executed.arguments) == {
        "expected_account_version": 5,
    }


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

    result = asyncio.run(manager.handle(identity, TurnObservations(
        "DP1234", interaction_id="interaction-1", interaction_version=1,
    )))

    observed_state, deterministic = understanding.calls[0]
    assert deterministic.kind is ResolutionKind.FILL_PENDING_INPUT
    assert observed_state.pending_interaction is None
    assert dict((item.name, item.value) for item in observed_state.workstreams[0].slots) == {
        "order_id": "DP1234",
    }
    assert result.state_after.version == observed_state.version + 1
    assert len(result.state_after.active_work_controls) == 1


def test_manager_persists_and_resumes_generic_read_work_from_typed_missing_input():
    identity = _identity("request-product-missing")
    store = InMemoryConversationStateStore()
    proposal = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "product-question",
            CommandKind.DELEGATE_TASK,
            "product_technical",
            "Answer a product question from governed evidence",
            (ArgumentValue.create("question", "这个商品支持我的设备吗？"),),
            ("knowledge.active_source",),
        ),),
        "PRODUCT_QUESTION_UNDERSTOOD",
    )
    worker = _ProductReferenceWorker()
    manager = TargetConversationManager(
        state_store=store,
        registry=build_default_capability_registry("tenant-target"),
        understanding=_ResumeAwareUnderstanding(proposal),
        orchestration=OrchestrationRuntime(
            direct_executor=_ReadExecutor(),
            domain_workers={"product_technical": worker},
        ),
    )

    first = asyncio.run(manager.handle(
        identity,
        TurnObservations("这个商品支持我的设备吗？"),
    ))

    pending = first.state_after.pending_interaction
    assert pending is not None
    assert len(pending.suspended_work_items) == 1
    suspended = pending.suspended_work_items[0]
    assert suspended.skill_hint is None
    assert tuple(field.field_name for field in pending.requested_fields) == (
        "product_reference",
    )
    restored_waiting = conversation_state_from_payload(
        conversation_state_to_payload(first.state_after)
    )
    assert restored_waiting == first.state_after
    assert restored_waiting.fingerprint == first.state_after.fingerprint

    resumed = asyncio.run(manager.handle(
        _identity("request-product-resume"),
        TurnObservations(
            "SKU-9",
            interaction_id=pending.interaction_id,
            interaction_version=pending.version,
            interaction_values=((
                suspended.work_item_id,
                "product_reference",
                "SKU-9",
            ),),
        ),
    ))

    assert resumed.deterministic.kind is ResolutionKind.FILL_PENDING_INPUT
    assert resumed.plan.route.reason_code == "PENDING_INPUT_RESUMED"
    assert resumed.state_after.pending_interaction is None
    assert resumed.board is not None
    assert resumed.board.results[0].status is AgentResultStatus.SUCCEEDED
    assert worker.calls == [
        {"question": "这个商品支持我的设备吗？"},
        {"product_reference": "SKU-9", "question": "这个商品支持我的设备吗？"},
    ]


def test_manager_aggregates_multi_domain_missing_inputs_into_one_interaction():
    proposal = TurnProposal(
        ProposalDisposition.RESOLVED,
        (
            CommandProposal(
                "order-question",
                CommandKind.DIRECT_TOOL,
                "order_logistics",
                "Query current order status",
                (ArgumentValue.create("order_id", "DP1234"),),
                ("order.current_state",),
                tool_id="order_lookup",
            ),
            CommandProposal(
                "product-question",
                CommandKind.DELEGATE_TASK,
                "product_technical",
                "Answer a product question from governed evidence",
                (ArgumentValue.create("question", "这个商品兼容吗？"),),
                ("knowledge.active_source",),
            ),
        ),
        "MULTI_DOMAIN_UNDERSTOOD",
    )
    state_store = InMemoryConversationStateStore()
    manager = TargetConversationManager(
        state_store=state_store,
        registry=build_default_capability_registry("tenant-target"),
        understanding=_Understanding(proposal),
        orchestration=OrchestrationRuntime(
            direct_executor=_OrderVerificationWorker(),
            domain_workers={"product_technical": _ProductReferenceWorker()},
        ),
    )

    result = asyncio.run(manager.handle(
        _identity("request-multi-missing"),
        TurnObservations("查订单，同时回答商品兼容性"),
    ))

    pending = result.state_after.pending_interaction
    assert pending is not None
    assert len(pending.suspended_work_items) == 2
    assert {
        field.field_name for field in pending.requested_fields
    } == {"verification_reference", "product_reference"}
    assert result.state_after.version == 2


def test_manager_commits_workflow_start_before_dispatch():
    identity = _identity("request-target-workflow")
    store = InMemoryConversationStateStore()
    proposal = TurnProposal(
        ProposalDisposition.RESOLVED,
        (CommandProposal(
            "refund",
            CommandKind.EXECUTE_ACTION,
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
                    CommandKind.EXECUTE_ACTION,
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
