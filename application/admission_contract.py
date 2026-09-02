"""M1 Admission v1 and thin execution projection contracts.

Application owns admission/dispatch. Runtime lifecycle remains an opaque reader;
this module deliberately does not duplicate Agent node transitions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, TypeAlias

from application.chat_application import (
    Accepted,
    Cancelled,
    ChatOutcome,
    Completed,
    Expired,
    Failed,
    HandedOff,
    NeedsInput,
    Reconciling,
)
from core.identity import InvocationKey, OperationKey, WorkflowRunId


class AdmissionContractError(ValueError):
    pass


class ExecutionProjectionConflict(AdmissionContractError):
    pass


class AdmissionStatus(str, Enum):
    START_QUEUED = "START_QUEUED"
    EXECUTION_BOUND = "EXECUTION_BOUND"
    EXPIRED_BEFORE_START = "EXPIRED_BEFORE_START"


LEGAL_ADMISSION_TRANSITIONS = {
    AdmissionStatus.START_QUEUED: frozenset({
        AdmissionStatus.EXECUTION_BOUND,
        AdmissionStatus.EXPIRED_BEFORE_START,
    }),
    AdmissionStatus.EXECUTION_BOUND: frozenset(),
    AdmissionStatus.EXPIRED_BEFORE_START: frozenset(),
}


class ExecutionStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    HANDED_OFF = "HANDED_OFF"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class SignalProducer(str, Enum):
    PRINCIPAL = "PRINCIPAL"
    MEDIA_RECEIPT = "MEDIA_RECEIPT"
    RECONCILIATION_RECEIPT = "RECONCILIATION_RECEIPT"


class ConsumeSignalStatus(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    EXPIRED = "EXPIRED"
    UNAUTHORIZED = "UNAUTHORIZED"


@dataclass(frozen=True)
class ExecutionPointer:
    runtime_kind: str
    runtime_version: str
    run_id: str

    def __post_init__(self) -> None:
        _required(self.runtime_kind, "runtime_kind")
        _required(self.runtime_version, "runtime_version")
        _required(self.run_id, "run_id")


@dataclass(frozen=True)
class AdmissionRecord:
    invocation_key: InvocationKey
    workflow_run_id: WorkflowRunId
    request_fingerprint: str
    status: AdmissionStatus
    version: int
    pinned_versions: Mapping[str, str]
    execution_pointer: ExecutionPointer | None = None

    def __post_init__(self) -> None:
        _sha256(self.request_fingerprint, "request_fingerprint")
        if self.version < 0:
            raise AdmissionContractError("admission version must be non-negative")
        if self.status is AdmissionStatus.EXECUTION_BOUND and not self.execution_pointer:
            raise AdmissionContractError("EXECUTION_BOUND requires an execution pointer")
        if self.status is not AdmissionStatus.EXECUTION_BOUND and self.execution_pointer:
            raise AdmissionContractError("only EXECUTION_BOUND may carry an execution pointer")


@dataclass(frozen=True)
class AdmissionCreated:
    record: AdmissionRecord


@dataclass(frozen=True)
class AdmissionExisting:
    record: AdmissionRecord


@dataclass(frozen=True)
class AdmissionConflict:
    existing: AdmissionRecord
    code: str = "IDEMPOTENCY_CONFLICT"


AdmissionResult: TypeAlias = AdmissionCreated | AdmissionExisting | AdmissionConflict


@dataclass(frozen=True)
class CasApplied:
    record: AdmissionRecord


@dataclass(frozen=True)
class CasAlreadyApplied:
    record: AdmissionRecord


@dataclass(frozen=True)
class CasRejected:
    current: AdmissionRecord
    code: str = "CAS_MISMATCH"


AdmissionCasResult: TypeAlias = CasApplied | CasAlreadyApplied | CasRejected


@dataclass(frozen=True)
class StartOutboxItem:
    outbox_id: OperationKey
    invocation_key: InvocationKey
    workflow_run_id: WorkflowRunId
    tenant_id: str
    user_id: str
    pinned_versions: Mapping[str, str]
    attempt: int
    available_at: str
    claimed_by: str = ""
    lease_until: str = ""


@dataclass(frozen=True)
class ClaimStart:
    worker_id: str
    now: str
    lease_until: str
    limit: int

    def __post_init__(self) -> None:
        _required(self.worker_id, "worker_id")
        _required(self.now, "now")
        _required(self.lease_until, "lease_until")
        if self.limit < 1:
            raise AdmissionContractError("claim limit must be positive")


class InvocationRepository(Protocol):
    def get(
        self,
        invocation_key: InvocationKey,
        *,
        tenant_id: str,
        user_id: str,
    ) -> AdmissionRecord | None: ...

    def compare_and_set(
        self,
        invocation_key: InvocationKey,
        *,
        expected_status: AdmissionStatus,
        expected_version: int,
        target_status: AdmissionStatus,
        execution_pointer: ExecutionPointer | None,
        tenant_id: str,
        user_id: str,
    ) -> AdmissionCasResult: ...


class AdmissionUnitOfWork(Protocol):
    def admit_with_start_outbox(
        self,
        record: AdmissionRecord,
        outbox_item: StartOutboxItem,
        *,
        inbound_turn: Mapping[str, Any],
        accepted_event: Mapping[str, Any],
    ) -> AdmissionResult: ...


class StartOutbox(Protocol):
    def claim(self, command: ClaimStart) -> tuple[StartOutboxItem, ...]: ...

    def acknowledge(self, outbox_id: OperationKey, *, worker_id: str) -> bool: ...

    def release(
        self, outbox_id: OperationKey, *, worker_id: str, available_at: str,
    ) -> bool: ...


class Dispatcher(Protocol):
    def dispatch(self, item: StartOutboxItem) -> AdmissionCasResult: ...


class PendingSignalStore(Protocol):
    def consume(
        self,
        *,
        signal_id: str,
        expected_version: int,
        inbound_turn_id: str,
    ) -> ConsumeSignalStatus: ...


@dataclass(frozen=True)
class ConsumeSignalCommand:
    signal_id: str
    expected_version: int
    inbound_turn_id: str
    content_fingerprint: str

    def __post_init__(self) -> None:
        _required(self.signal_id, "signal_id")
        _required(self.inbound_turn_id, "inbound_turn_id")
        if self.expected_version < 0:
            raise AdmissionContractError("signal version must be non-negative")
        _sha256(self.content_fingerprint, "content_fingerprint")


def classify_signal_consumption(
    existing: ConsumeSignalCommand | None,
    command: ConsumeSignalCommand,
    *,
    authorized: bool,
    expired: bool,
) -> ConsumeSignalStatus:
    """Pure contract used by any PendingSignalStore implementation."""
    if not authorized:
        return ConsumeSignalStatus.UNAUTHORIZED
    if existing is not None:
        return (
            ConsumeSignalStatus.ALREADY_APPLIED
            if existing == command else ConsumeSignalStatus.IDEMPOTENCY_CONFLICT
        )
    if expired:
        return ConsumeSignalStatus.EXPIRED
    return ConsumeSignalStatus.APPLIED


@dataclass(frozen=True)
class PendingSignalView:
    signal_id: str
    producer: SignalProducer
    kind: str
    expires_at: str
    interaction_publication_id: str = ""
    next_poll_after: float = 1.0


@dataclass(frozen=True)
class FinalPublicationCommitted:
    """Atomic response + outbound event + delivery outbox commit evidence."""

    response_id: str
    response: Mapping[str, Any]
    outbound_event_id: str
    delivery_outbox_id: str

    def __post_init__(self) -> None:
        _required(self.response_id, "response_id")
        _required(self.outbound_event_id, "outbound_event_id")
        _required(self.delivery_outbox_id, "delivery_outbox_id")


@dataclass(frozen=True)
class ExecutionFacts:
    admission: AdmissionRecord
    final_response: FinalPublicationCommitted | None = None
    handoff: HandedOff | None = None
    cancellation: Cancelled | None = None
    failure: Failed | None = None
    pending_signal: PendingSignalView | None = None
    runtime_running: bool = False


@dataclass(frozen=True)
class ExecutionView:
    status: ExecutionStatus | None
    outcome: ChatOutcome


class ExecutionViewProjector:
    """Terminal facts > PendingSignal > runtime running > admission."""

    def project(self, facts: ExecutionFacts) -> ExecutionView:
        terminal = tuple(item for item in (
            facts.final_response, facts.handoff, facts.cancellation, facts.failure,
        ) if item is not None)
        if len(terminal) > 1:
            raise ExecutionProjectionConflict("conflicting terminal execution facts")
        if terminal:
            fact = terminal[0]
            outcome: ChatOutcome = (
                Completed(fact.response_id, fact.response)
                if isinstance(fact, FinalPublicationCommitted) else fact
            )
            status = {
                Completed: ExecutionStatus.COMPLETED,
                HandedOff: ExecutionStatus.HANDED_OFF,
                Cancelled: ExecutionStatus.CANCELLED,
                Failed: ExecutionStatus.FAILED,
            }[type(outcome)]
            return ExecutionView(status, outcome)

        run_id = str(facts.admission.workflow_run_id)
        signal = facts.pending_signal
        if signal is not None:
            if signal.producer is SignalProducer.PRINCIPAL:
                if not signal.interaction_publication_id:
                    raise ExecutionProjectionConflict(
                        "principal signal lacks an interaction publication"
                    )
                outcome: ChatOutcome = NeedsInput(
                    workflow_run_id=run_id,
                    signal_id=signal.signal_id,
                    kind=signal.kind,
                    expires_at=signal.expires_at,
                    interaction_publication_id=signal.interaction_publication_id,
                )
            elif signal.producer is SignalProducer.MEDIA_RECEIPT:
                outcome = Accepted(run_id, {
                    "execution": "WAITING",
                    "waiting_for": "MEDIA_RECEIPT",
                })
            elif signal.producer is SignalProducer.RECONCILIATION_RECEIPT:
                outcome = Reconciling(run_id, {
                    "execution": "WAITING",
                    "waiting_for": "RECONCILIATION_RECEIPT",
                }, signal.next_poll_after)
            else:  # pragma: no cover - Enum construction is already fail closed.
                raise ExecutionProjectionConflict("unsupported signal producer")
            return ExecutionView(ExecutionStatus.WAITING, outcome)

        if facts.runtime_running:
            return ExecutionView(ExecutionStatus.RUNNING, Accepted(run_id, {
                "execution": "RUNNING",
            }))
        if facts.admission.status is AdmissionStatus.EXPIRED_BEFORE_START:
            return ExecutionView(None, Expired(run_id, "ADMISSION", True))
        return ExecutionView(None, Accepted(run_id, {
            "admission": facts.admission.status.value,
        }))


RUN_STATUS_PROJECTION_CONTRACT = {
    "RUNNING": "ExecutionView.RUNNING",
    "WAITING_APPROVAL": "ExecutionView.WAITING:PendingSignal.PRINCIPAL",
    "COMPLETED": "ExecutionView.COMPLETED:requires_atomic_final_publication",
    "BLOCKED": "ExecutionView.FAILED:reason=BLOCKED",
    "TOOL_ERROR": "ExecutionView.FAILED:reason=TOOL_ERROR",
    "MAX_STEPS": "ExecutionView.FAILED:reason=BUDGET_EXCEEDED",
    "CANCELLED": "ExecutionView.CANCELLED",
    "EXPIRED": "Expired(stage=RUNTIME,new_request_required=true)",
}


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_admission_transition(
    current: AdmissionStatus, target: AdmissionStatus,
) -> None:
    if target not in LEGAL_ADMISSION_TRANSITIONS[current]:
        raise AdmissionContractError(
            f"illegal admission transition: {current.value}->{target.value}"
        )


def _required(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AdmissionContractError(f"{field} is required")
    return text


def _sha256(value: object, field: str) -> str:
    text = str(value or "").lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise AdmissionContractError(f"{field} must be a SHA-256 digest")
    return text
