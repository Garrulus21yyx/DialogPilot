"""Governed business-write execution with receipts and reconciliation.

The runtime never retries an unknown side effect.  The operation ledger owns
the monotonic state, while the tool port owns the business side effect and the
reconciler owns observation of an uncertain outcome.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from typing import Mapping, Protocol

from application.agent_result import AgentResult, AgentResultStatus, FactRecord, ReceiptRef
from application.capability_registry import CapabilityEffect
from application.orchestration_runtime import AgentContextView
from application.work_item import ControlMode, WorkItem


class WriteWorkflowError(ValueError):
    pass


class OperationConflict(WriteWorkflowError):
    pass


class OperationStatus(str, Enum):
    PLANNED = "PLANNED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class WriteOutcomeStatus(str, Enum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class ApprovalGrant:
    binding_ref: str
    operation_key: str
    target_entity_version: str
    approved: bool
    actor_ref: str

    def __post_init__(self) -> None:
        if any(not str(value or "").strip() for value in (
            self.binding_ref,
            self.operation_key,
            self.target_entity_version,
            self.actor_ref,
        )):
            raise WriteWorkflowError("approval grant is incomplete")


@dataclass(frozen=True)
class WriteToolOutcome:
    status: WriteOutcomeStatus
    receipt_id: str = ""
    receipt_schema_version: str = ""
    reason_code: str = ""
    facts: tuple[FactRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))
        if self.facts and self.status is not WriteOutcomeStatus.COMMITTED:
            raise WriteWorkflowError("only a committed write outcome carries business observations")
        if not self.reason_code.strip():
            raise WriteWorkflowError("write outcome reason is required")
        if self.status is WriteOutcomeStatus.COMMITTED and any(
            not value.strip() for value in (self.receipt_id, self.receipt_schema_version)
        ):
            raise WriteWorkflowError("committed outcome requires a typed receipt")


@dataclass(frozen=True)
class OperationRecord:
    operation_key: str
    work_item_fingerprint: str
    status: OperationStatus
    version: int
    attempts: int
    receipt_id: str = ""
    receipt_schema_version: str = ""
    reason_code: str = ""
    facts: tuple[FactRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))
        if self.facts and self.status is not OperationStatus.COMMITTED:
            raise WriteWorkflowError("only a committed operation carries business observations")
        if not self.operation_key.strip() or not self.work_item_fingerprint.strip():
            raise WriteWorkflowError("operation identity is required")
        if self.version < 1 or self.attempts < 0:
            raise WriteWorkflowError("operation counters are invalid")
        if self.status is OperationStatus.COMMITTED and any(
            not value.strip() for value in (self.receipt_id, self.receipt_schema_version)
        ):
            raise WriteWorkflowError("committed operation requires a receipt")


class OperationLedger(Protocol):
    def acquire(self, item: WorkItem) -> OperationRecord: ...

    def compare_and_set(
        self,
        current: OperationRecord,
        next_record: OperationRecord,
    ) -> bool: ...


class WriteToolPort(Protocol):
    async def execute(
        self,
        item: WorkItem,
        *,
        tool_id: str,
        arguments: Mapping[str, object],
        operation_key: str,
    ) -> WriteToolOutcome: ...


class ReconciliationPort(Protocol):
    async def reconcile(
        self,
        item: WorkItem,
        *,
        operation_key: str,
    ) -> WriteToolOutcome: ...


class InMemoryOperationLedger:
    """Reference CAS ledger; production adapters must preserve this algebra."""

    def __init__(self) -> None:
        self._records: dict[str, OperationRecord] = {}
        self._lock = RLock()

    def acquire(self, item: WorkItem) -> OperationRecord:
        operation_key = str(item.operation_key or "")
        if not operation_key:
            raise WriteWorkflowError("write work item has no operation key")
        with self._lock:
            current = self._records.get(operation_key)
            if current is None:
                current = OperationRecord(
                    operation_key,
                    item.operation_fingerprint,
                    OperationStatus.PLANNED,
                    1,
                    0,
                    reason_code="OPERATION_REGISTERED",
                )
                self._records[operation_key] = current
            elif current.work_item_fingerprint != item.operation_fingerprint:
                raise OperationConflict("operation key is bound to another work item")
            return current

    def compare_and_set(
        self,
        current: OperationRecord,
        next_record: OperationRecord,
    ) -> bool:
        if current.operation_key != next_record.operation_key:
            raise WriteWorkflowError("operation CAS keys differ")
        if next_record.version != current.version + 1:
            raise WriteWorkflowError("operation CAS must advance one version")
        if current.work_item_fingerprint != next_record.work_item_fingerprint:
            raise OperationConflict("operation binding cannot change")
        with self._lock:
            stored = self._records.get(current.operation_key)
            if stored != current:
                return False
            self._records[current.operation_key] = next_record
            return True


class GovernedWriteRuntime:
    version = "governed-write-runtime-v1"

    def __init__(
        self,
        *,
        ledger: OperationLedger,
        tool_port: WriteToolPort,
        reconciliation_port: ReconciliationPort,
        approval_grants: Mapping[str, ApprovalGrant],
    ) -> None:
        self._ledger = ledger
        self._tool_port = tool_port
        self._reconciliation_port = reconciliation_port
        self._approval_grants = approval_grants
        self._locks: dict[str, asyncio.Lock] = {}

    async def __call__(self, context: AgentContextView) -> AgentResult:
        item = context.work_item
        self._validate_item(item)
        operation_key = str(item.operation_key)
        lock = self._locks.setdefault(operation_key, asyncio.Lock())
        async with lock:
            record = self._ledger.acquire(item)
            if record.status is OperationStatus.COMMITTED:
                return self._committed_result(item, record, "IDEMPOTENT_RECEIPT_REPLAY")
            if record.status in {
                OperationStatus.OUTCOME_UNKNOWN,
                OperationStatus.RECONCILING,
                OperationStatus.EXECUTING,
            }:
                return await self._reconcile(item, record)
            if record.status is OperationStatus.CANCELLED:
                return self._result(item, AgentResultStatus.CANCELLED, record.reason_code)

            grant = self._approval_grants.get(str(item.approval_binding))
            if grant is None:
                waiting = self._transition(
                    record, OperationStatus.WAITING_APPROVAL, "APPROVAL_REQUIRED",
                )
                return self._result(
                    item, AgentResultStatus.WAITING_APPROVAL, waiting.reason_code,
                )
            self._validate_grant(item, grant)
            if not grant.approved:
                cancelled = self._transition(
                    record, OperationStatus.CANCELLED, "APPROVAL_DENIED",
                )
                return self._result(
                    item, AgentResultStatus.CANCELLED, cancelled.reason_code,
                )

            executing = self._transition(
                record,
                OperationStatus.EXECUTING,
                "WRITE_EXECUTION_STARTED",
                attempts=record.attempts + 1,
            )
            try:
                outcome = await self._tool_port.execute(
                    item,
                    tool_id=self._write_tool(item),
                    arguments={argument.name: argument.value for argument in item.arguments},
                    operation_key=operation_key,
                )
            except Exception:
                outcome = WriteToolOutcome(
                    WriteOutcomeStatus.OUTCOME_UNKNOWN,
                    reason_code="WRITE_TRANSPORT_FAILED",
                )
            terminal = self._record_outcome(executing, outcome)
            if terminal.status is OperationStatus.COMMITTED:
                return self._committed_result(item, terminal, outcome.reason_code)
            if terminal.status is OperationStatus.NOT_COMMITTED:
                return self._result(
                    item,
                    AgentResultStatus.RETRYABLE_FAILURE,
                    outcome.reason_code,
                    retryable=True,
                )
            return self._result(
                item, AgentResultStatus.RECONCILING, outcome.reason_code,
            )

    async def _reconcile(
        self,
        item: WorkItem,
        record: OperationRecord,
    ) -> AgentResult:
        if record.status is not OperationStatus.RECONCILING:
            record = self._transition(
                record, OperationStatus.RECONCILING, "RECONCILIATION_STARTED",
            )
        try:
            outcome = await self._reconciliation_port.reconcile(
                item,
                operation_key=str(item.operation_key),
            )
        except Exception:
            return self._result(
                item,
                AgentResultStatus.RECONCILING,
                "RECONCILIATION_UNAVAILABLE",
            )
        terminal = self._record_outcome(record, outcome)
        if terminal.status is OperationStatus.COMMITTED:
            return self._committed_result(item, terminal, outcome.reason_code)
        if terminal.status is OperationStatus.NOT_COMMITTED:
            return self._result(
                item,
                AgentResultStatus.RETRYABLE_FAILURE,
                outcome.reason_code,
                retryable=True,
            )
        return self._result(
            item, AgentResultStatus.RECONCILING, outcome.reason_code,
        )

    def _record_outcome(
        self,
        record: OperationRecord,
        outcome: WriteToolOutcome,
    ) -> OperationRecord:
        status = {
            WriteOutcomeStatus.COMMITTED: OperationStatus.COMMITTED,
            WriteOutcomeStatus.NOT_COMMITTED: OperationStatus.NOT_COMMITTED,
            WriteOutcomeStatus.OUTCOME_UNKNOWN: OperationStatus.OUTCOME_UNKNOWN,
        }[outcome.status]
        return self._transition(
            record,
            status,
            outcome.reason_code,
            receipt_id=outcome.receipt_id,
            receipt_schema_version=outcome.receipt_schema_version,
            facts=outcome.facts,
        )

    def _transition(
        self,
        current: OperationRecord,
        status: OperationStatus,
        reason_code: str,
        **changes,
    ) -> OperationRecord:
        if status not in _LEGAL_TRANSITIONS[current.status]:
            raise OperationConflict(
                f"illegal operation transition {current.status.value}->{status.value}"
            )
        next_record = replace(
            current,
            status=status,
            version=current.version + 1,
            reason_code=reason_code,
            **changes,
        )
        if not self._ledger.compare_and_set(current, next_record):
            raise OperationConflict("operation changed concurrently")
        return next_record

    @staticmethod
    def _validate_item(item: WorkItem) -> None:
        if item.control_mode not in {ControlMode.WORKFLOW, ControlMode.ACTION}:
            raise WriteWorkflowError("write runtime accepts only governed action work")
        if item.effect is not CapabilityEffect.WRITE:
            raise WriteWorkflowError("write runtime accepts only WRITE effects")
        if not item.operation_key or not item.approval_binding:
            raise WriteWorkflowError("write safety bindings are required")

    @staticmethod
    def _validate_grant(item: WorkItem, grant: ApprovalGrant) -> None:
        if grant.binding_ref != item.approval_binding:
            raise OperationConflict("approval binding differs from work item")
        if grant.operation_key != item.operation_key:
            raise OperationConflict("approval is bound to another operation")
        if grant.target_entity_version != item.target_entity_version:
            raise OperationConflict("approval target version is stale")

    @staticmethod
    def _write_tool(item: WorkItem) -> str:
        reconciliation_tool = (
            item.reconciliation.tool_id if item.reconciliation else None
        )
        write_tools = tuple(
            tool for tool in item.allowed_tools if tool != reconciliation_tool
        )
        if len(write_tools) != 1:
            raise WriteWorkflowError("write step must pin exactly one tool")
        return write_tools[0]

    def _committed_result(
        self,
        item: WorkItem,
        record: OperationRecord,
        reason_code: str,
    ) -> AgentResult:
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            reason_code,
            self.version,
            facts=record.facts,
            evidence_refs=tuple(fact.source_ref for fact in record.facts),
            action_receipts=tuple(
                ReceiptRef(
                    record.receipt_id,
                    record.receipt_schema_version,
                    record.operation_key,
                    WriteOutcomeStatus.COMMITTED.value,
                    requirement_id,
                )
                for requirement_id in item.requirement_ids
            ),
        )

    def _result(
        self,
        item: WorkItem,
        status: AgentResultStatus,
        reason_code: str,
        *,
        retryable: bool = False,
    ) -> AgentResult:
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            status,
            reason_code,
            self.version,
            retryable=retryable,
        )


_LEGAL_TRANSITIONS = {
    OperationStatus.PLANNED: {
        OperationStatus.WAITING_APPROVAL,
        OperationStatus.EXECUTING,
        OperationStatus.CANCELLED,
    },
    OperationStatus.WAITING_APPROVAL: {
        OperationStatus.WAITING_APPROVAL,
        OperationStatus.EXECUTING,
        OperationStatus.CANCELLED,
    },
    OperationStatus.EXECUTING: {
        OperationStatus.COMMITTED,
        OperationStatus.NOT_COMMITTED,
        OperationStatus.OUTCOME_UNKNOWN,
        OperationStatus.RECONCILING,
    },
    OperationStatus.OUTCOME_UNKNOWN: {OperationStatus.RECONCILING},
    OperationStatus.RECONCILING: {
        OperationStatus.COMMITTED,
        OperationStatus.NOT_COMMITTED,
        OperationStatus.OUTCOME_UNKNOWN,
    },
    OperationStatus.NOT_COMMITTED: {OperationStatus.EXECUTING},
    OperationStatus.COMMITTED: set(),
    OperationStatus.CANCELLED: set(),
}
