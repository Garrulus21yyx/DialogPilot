import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from application.agent_result import AgentResultStatus
from application.capability_registry import (
    ActionReconciliationDefinition,
    ApprovalPolicy,
    CapabilityEffect,
    CapabilityRisk,
)
from application.orchestration_runtime import AgentContextView, OrchestrationRuntime
from application.work_item import ArgumentValue, ControlMode, WorkItem, WorkPlan
from application.write_workflow import (
    ApprovalGrant,
    GovernedWriteRuntime,
    InMemoryOperationLedger,
    OperationConflict,
    OperationStatus,
    WriteOutcomeStatus,
    WriteToolOutcome,
)
from application.work_control import WorkSuperseded
from application.conversation_store import ConversationScope
from core.identity import TenantId, UserId, ConversationId
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_target_runtime import PostgresOperationLedger


@pytest.fixture(autouse=True, params=(ControlMode.WORKFLOW, ControlMode.ACTION))
def action_execution_mode(request, monkeypatch):
    factory = _item

    def item(*args, **kwargs):
        work = factory(*args, **kwargs)
        return replace(work, control_mode=request.param,
                       flow_ref=work.flow_ref if request.param is ControlMode.WORKFLOW else None)

    monkeypatch.setitem(globals(), "_item", item)


@pytest.fixture(params=("memory", "postgres"))
def ledger_factory(request):
    if request.param == "memory":
        ledger = InMemoryOperationLedger()
        yield lambda: ledger
        return
    database_url = request.getfixturevalue("postgres_database_url")
    PostgresMigrationRunner(database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(database_url, min_size=1, max_size=4))
    pool.open()
    scope = ConversationScope(
        TenantId("workflow-test"), UserId("user-a"), ConversationId(uuid4().hex),
    )
    try:
        yield lambda: PostgresOperationLedger(pool, scope)
    finally:
        pool.close()


def _item(operation_key="operation-1"):
    return WorkItem(
        "refund-write-1",
        "billing_refund",
        "Create a refund request",
        ControlMode.WORKFLOW,
        ("refund_request_create", "refund_status"),
        (),
        (
            ArgumentValue.create("order_id", "DP1234"),
            ArgumentValue.create("expected_order_version", 7),
            ArgumentValue.create("reason", "requested by customer"),
        ),
        ("refund.request_action",),
        (),
        CapabilityEffect.WRITE,
        CapabilityRisk.HIGH,
        "refund-receipt-v1",
        "refund-write:v1",
        3,
        "registry:v1:test",
        5,
        4,
        flow_ref="execute_refund:v1",
        operation_key=operation_key,
        approval_binding="approval-1:v1",
        target_entity_version="order:DP1234:v7",
        reconciliation=ActionReconciliationDefinition(
            "refund_status", "refund.current_state",
            "operation_key", "operation_key", ("order_id",), "refund_id",
        ),
        aggregate_ref="order:DP1234",
        action_ref="refund.request.create:v1",
        approval_policy=ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
    )


def _context(item=None):
    return AgentContextView(item or _item(), "refund it", (), (), (), 2000)


def _grant(**changes):
    values = {
        "binding_ref": "approval-1:v1",
        "operation_key": "operation-1",
        "target_entity_version": "order:DP1234:v7",
        "approved": True,
        "actor_ref": "user:user-a",
    }
    values.update(changes)
    return ApprovalGrant(**values)


class ToolPort:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def execute(self, item, *, tool_id, arguments, operation_key):
        self.calls.append((tool_id, arguments, operation_key))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Reconciler:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def reconcile(self, item, *, operation_key):
        self.calls.append(operation_key)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _runtime(tool, reconciler, grants=None, ledger=None):
    return GovernedWriteRuntime(
        ledger=ledger or InMemoryOperationLedger(),
        tool_port=tool,
        reconciliation_port=reconciler,
        approval_grants=grants or {},
    )


def test_missing_approval_waits_without_calling_write_tool(ledger_factory):
    tool = ToolPort([])
    ledger = ledger_factory()
    runtime = _runtime(tool, Reconciler([]), ledger=ledger)

    result = asyncio.run(runtime(_context()))

    assert result.status is AgentResultStatus.WAITING_APPROVAL
    assert result.action_receipts == ()
    assert tool.calls == []
    assert ledger.acquire(_item()).status is OperationStatus.WAITING_APPROVAL


@pytest.mark.parametrize("status", (
    OperationStatus.EXECUTING, OperationStatus.OUTCOME_UNKNOWN,
    OperationStatus.RECONCILING,
))
def test_recreated_runtime_reconciles_unfinished_writes_without_resubmission(
    ledger_factory, status,
):
    ledger = ledger_factory()
    planned = ledger.acquire(_item())
    unfinished = replace(planned, status=status, version=2, attempts=1)
    assert ledger.compare_and_set(planned, unfinished)
    tool = ToolPort([])
    reconciler = Reconciler([WriteToolOutcome(
        WriteOutcomeStatus.COMMITTED, "refund-R1", "refund-receipt-v1", "FOUND",
    )])
    recovered = _runtime(tool, reconciler, ledger=ledger_factory())

    result = asyncio.run(recovered(_context()))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.action_receipts[0].receipt_id == "refund-R1"
    assert tool.calls == []
    assert reconciler.calls == ["operation-1"]
    assert ledger.acquire(_item()).status is OperationStatus.COMMITTED


def test_operation_identity_is_scoped_and_each_scope_keeps_its_binding(ledger_factory):
    ledger = ledger_factory()
    if not isinstance(ledger, PostgresOperationLedger):
        pytest.skip("In-memory ledgers are isolated by instance, not database scope")
    from concurrent.futures import ThreadPoolExecutor

    scopes = (
        ledger.scope,
        replace(ledger.scope, tenant_id=TenantId("another-tenant")),
        replace(ledger.scope, user_id=UserId("another-user")),
        replace(ledger.scope, conversation_id=ConversationId("another-" + str(ledger.scope.conversation_id))),
    )
    ledgers = [PostgresOperationLedger(ledger.pool, scope) for scope in scopes]
    items = [replace(_item(), arguments=(ArgumentValue.create("order_id", f"DP{i}"),))
             for i in range(len(scopes))]
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(lambda pair: pair[0].acquire(pair[1]), zip(ledgers, items)))
    for owner, item, record in zip(ledgers, items, records):
        assert owner.acquire(item) == record
        changed = replace(item, arguments=(ArgumentValue.create("order_id", "CHANGED"),))
        with pytest.raises(OperationConflict):
            owner.acquire(changed)


def test_committed_receipt_is_replayed_without_duplicate_side_effect(ledger_factory):
    committed = WriteToolOutcome(
        WriteOutcomeStatus.COMMITTED,
        "refund-R1",
        "refund-receipt-v1",
        "REFUND_CREATED",
    )
    tool = ToolPort([committed])
    runtime = _runtime(tool, Reconciler([]), {"approval-1:v1": _grant()}, ledger=ledger_factory())

    first = asyncio.run(runtime(_context()))
    recovered = _runtime(tool, Reconciler([]), ledger=ledger_factory())
    replay = asyncio.run(recovered(_context()))

    assert first.status is AgentResultStatus.SUCCEEDED
    assert replay.reason_code == "IDEMPOTENT_RECEIPT_REPLAY"
    assert first.action_receipts == replay.action_receipts
    assert len(tool.calls) == 1


def test_unknown_write_outcome_reconciles_before_any_possible_retry(ledger_factory):
    tool = ToolPort([TimeoutError("connection dropped")])
    reconciler = Reconciler([WriteToolOutcome(
        WriteOutcomeStatus.COMMITTED,
        "refund-R1",
        "refund-receipt-v1",
        "RECONCILED_COMMITTED",
    )])
    runtime = _runtime(tool, reconciler, {"approval-1:v1": _grant()}, ledger=ledger_factory())

    unknown = asyncio.run(runtime(_context()))
    reconciled = asyncio.run(runtime(_context()))

    assert unknown.status is AgentResultStatus.RECONCILING
    assert reconciled.status is AgentResultStatus.SUCCEEDED
    assert len(tool.calls) == 1
    assert reconciler.calls == ["operation-1"]


def test_correction_after_write_submission_enters_reconciliation(ledger_factory) -> None:
    tool = ToolPort([WorkSuperseded("target changed after submission grant")])
    reconciler = Reconciler([WriteToolOutcome(
        WriteOutcomeStatus.NOT_COMMITTED,
        reason_code="RECONCILED_NOT_COMMITTED",
    )])
    runtime = _runtime(tool, reconciler, {"approval-1:v1": _grant()}, ledger=ledger_factory())

    interrupted = asyncio.run(runtime(_context()))
    reconciled = asyncio.run(runtime(_context()))

    assert interrupted.status is AgentResultStatus.RECONCILING
    assert reconciled.status is AgentResultStatus.RETRYABLE_FAILURE
    assert len(tool.calls) == 1
    assert reconciler.calls == ["operation-1"]


def test_definitive_not_committed_result_allows_a_controlled_retry(ledger_factory):
    not_committed = WriteToolOutcome(
        WriteOutcomeStatus.NOT_COMMITTED,
        reason_code="UPSTREAM_REJECTED_BEFORE_COMMIT",
    )
    committed = WriteToolOutcome(
        WriteOutcomeStatus.COMMITTED,
        "refund-R1",
        "refund-receipt-v1",
        "REFUND_CREATED",
    )
    tool = ToolPort([not_committed, committed])
    runtime = _runtime(tool, Reconciler([]), {"approval-1:v1": _grant()}, ledger=ledger_factory())

    first = asyncio.run(runtime(_context()))
    second = asyncio.run(runtime(_context()))

    assert first.status is AgentResultStatus.RETRYABLE_FAILURE
    assert first.retryable is True
    assert second.status is AgentResultStatus.SUCCEEDED
    assert len(tool.calls) == 2


def test_stale_approval_target_version_fails_closed_before_execution(ledger_factory):
    tool = ToolPort([])
    runtime = _runtime(
        tool,
        Reconciler([]),
        {"approval-1:v1": _grant(target_entity_version="order:DP1234:v6")},
        ledger=ledger_factory(),
    )

    with pytest.raises(OperationConflict, match="target version is stale"):
        asyncio.run(runtime(_context()))
    assert tool.calls == []


def test_reconciliation_failure_remains_typed_and_never_resubmits_write(ledger_factory):
    tool = ToolPort([TimeoutError("connection dropped")])
    reconciler = Reconciler([RuntimeError("status endpoint unavailable")])
    runtime = _runtime(tool, reconciler, {"approval-1:v1": _grant()}, ledger=ledger_factory())

    first = asyncio.run(runtime(_context()))
    second = asyncio.run(runtime(_context()))

    assert first.status is AgentResultStatus.RECONCILING
    assert second.status is AgentResultStatus.RECONCILING
    assert second.reason_code == "RECONCILIATION_UNAVAILABLE"
    assert len(tool.calls) == 1


def test_concurrent_duplicate_operation_executes_the_side_effect_once(ledger_factory):
    committed = WriteToolOutcome(
        WriteOutcomeStatus.COMMITTED,
        "refund-R1",
        "refund-receipt-v1",
        "REFUND_CREATED",
    )
    tool = ToolPort([committed])
    runtime = _runtime(tool, Reconciler([]), {"approval-1:v1": _grant()}, ledger=ledger_factory())

    async def run_both():
        return await asyncio.gather(runtime(_context()), runtime(_context()))

    results = asyncio.run(run_both())

    assert all(item.status is AgentResultStatus.SUCCEEDED for item in results)
    assert len(tool.calls) == 1


def test_operation_binding_survives_a_new_turn_snapshot_but_rejects_changed_arguments(ledger_factory):
    ledger = ledger_factory()
    first = _item()
    original = ledger.acquire(first)
    next_turn = WorkItem(**{
        **first.__dict__,
        "work_item_id": "refund-write-reconcile-2",
        "objective": "Reconcile the refund outcome",
        "state_snapshot_version": first.state_snapshot_version + 1,
        "timeout_seconds": first.timeout_seconds + 5,
    })

    assert ledger.acquire(next_turn) == original

    changed = WorkItem(**{
        **next_turn.__dict__,
        "arguments": (
            ArgumentValue.create("order_id", "DP9999"),
            *next_turn.arguments[1:],
        ),
    })

    with pytest.raises(OperationConflict, match="another work item"):
        ledger.acquire(changed)


def test_langgraph_workflow_worker_uses_same_receipt_backed_runtime(ledger_factory):
    committed = WriteToolOutcome(
        WriteOutcomeStatus.COMMITTED,
        "refund-R1",
        "refund-receipt-v1",
        "REFUND_CREATED",
    )
    tool = ToolPort([committed])
    write_runtime = _runtime(
        tool,
        Reconciler([]),
        {"approval-1:v1": _grant()},
        ledger=ledger_factory(),
    )

    async def unused_direct(_context):
        raise AssertionError("workflow must not use direct executor")

    graph_runtime = OrchestrationRuntime(
        direct_executor=unused_direct,
        domain_workers={"billing_refund": write_runtime},
    )
    item = _item()
    board = asyncio.run(graph_runtime.execute(
        WorkPlan((item,), item.work_item_id),
        current_message="refund it",
    ))

    assert board.complete is True
    assert board.missing_requirement_ids == ()
    assert board.results[0].action_receipts[0].receipt_id == "refund-R1"
    assert len(tool.calls) == 1
