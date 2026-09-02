"""Admission CAS, execution projection and M3 compatibility contract tests."""
import itertools

import pytest

from application.admission_contract import (
    LEGAL_ADMISSION_TRANSITIONS,
    M3_COMPATIBILITY_CUTOVER,
    AdmissionContractError,
    AdmissionRecord,
    AdmissionStatus,
    ConsumeSignalCommand,
    ConsumeSignalStatus,
    ExecutionFacts,
    ExecutionPointer,
    ExecutionProjectionConflict,
    ExecutionStatus,
    ExecutionViewProjector,
    FinalPublicationCommitted,
    PendingSignalView,
    SignalProducer,
    classify_signal_consumption,
    request_fingerprint,
    validate_admission_transition,
)
from application.chat_application import (
    Accepted,
    Cancelled,
    Completed,
    Expired,
    Failed,
    HandedOff,
    NeedsInput,
    Reconciling,
)
from agents.run_store import RunStatus
from core.identity import InvocationKey, WorkflowRunId


def _admission(status=AdmissionStatus.START_QUEUED):
    pointer = ExecutionPointer("compat", "v1", "run-1") if (
        status is AdmissionStatus.EXECUTION_BOUND
    ) else None
    return AdmissionRecord(
        invocation_key=InvocationKey("invocation:v1:" + "a" * 64),
        workflow_run_id=WorkflowRunId("run-1"),
        request_fingerprint=request_fingerprint({"message": "hello"}),
        status=status,
        version=0,
        pinned_versions={"bundle": "v1", "runtime": "compat-v1"},
        execution_pointer=pointer,
    )


def test_admission_state_machine_has_only_two_cas_transitions():
    assert LEGAL_ADMISSION_TRANSITIONS == {
        AdmissionStatus.START_QUEUED: frozenset({
            AdmissionStatus.EXECUTION_BOUND,
            AdmissionStatus.EXPIRED_BEFORE_START,
        }),
        AdmissionStatus.EXECUTION_BOUND: frozenset(),
        AdmissionStatus.EXPIRED_BEFORE_START: frozenset(),
    }
    for current, target in itertools.product(AdmissionStatus, repeat=2):
        if target in LEGAL_ADMISSION_TRANSITIONS[current]:
            validate_admission_transition(current, target)
        else:
            with pytest.raises(AdmissionContractError, match="illegal"):
                validate_admission_transition(current, target)


def test_request_fingerprint_is_canonical_and_changes_with_content():
    assert request_fingerprint({"message": "hello", "tenant": "a"}) == (
        request_fingerprint({"tenant": "a", "message": "hello"})
    )
    assert request_fingerprint({"message": "hello", "tenant": "a"}) != (
        request_fingerprint({"message": "changed", "tenant": "a"})
    )


def test_pending_signal_consumption_is_idempotent_and_fail_closed():
    command = ConsumeSignalCommand("signal", 2, "turn", "a" * 64)
    assert classify_signal_consumption(
        None, command, authorized=True, expired=False,
    ) is ConsumeSignalStatus.APPLIED
    assert classify_signal_consumption(
        command, command, authorized=True, expired=True,
    ) is ConsumeSignalStatus.ALREADY_APPLIED
    changed = ConsumeSignalCommand("signal", 2, "turn", "b" * 64)
    assert classify_signal_consumption(
        command, changed, authorized=True, expired=False,
    ) is ConsumeSignalStatus.IDEMPOTENCY_CONFLICT
    assert classify_signal_consumption(
        command, command, authorized=False, expired=False,
    ) is ConsumeSignalStatus.UNAUTHORIZED
    assert classify_signal_consumption(
        None, command, authorized=True, expired=True,
    ) is ConsumeSignalStatus.EXPIRED


def test_execution_bound_is_the_only_admission_with_an_opaque_pointer():
    with pytest.raises(AdmissionContractError, match="requires"):
        AdmissionRecord(
            invocation_key=_admission().invocation_key,
            workflow_run_id=WorkflowRunId("run-1"),
            request_fingerprint="a" * 64,
            status=AdmissionStatus.EXECUTION_BOUND,
            version=0,
            pinned_versions={},
        )
    assert _admission(AdmissionStatus.EXECUTION_BOUND).execution_pointer.run_id == "run-1"


def test_projection_priority_is_terminal_then_waiting_running_and_admission():
    projector = ExecutionViewProjector()
    admission = _admission(AdmissionStatus.EXECUTION_BOUND)
    committed = FinalPublicationCommitted(
        "response-1", {"response_id": "response-1"}, "event-1", "outbox-1",
    )
    terminal = projector.project(ExecutionFacts(
        admission=admission,
        final_response=committed,
        pending_signal=PendingSignalView(
            "signal-1", SignalProducer.PRINCIPAL, "approval", "later", "pub-1",
        ),
        runtime_running=True,
    ))
    assert terminal.status is ExecutionStatus.COMPLETED
    assert terminal.outcome == Completed("response-1", {"response_id": "response-1"})

    waiting = projector.project(ExecutionFacts(
        admission=admission,
        pending_signal=PendingSignalView(
            "signal-1", SignalProducer.PRINCIPAL, "approval", "later", "pub-1",
        ),
        runtime_running=True,
    ))
    assert waiting.status is ExecutionStatus.WAITING
    assert isinstance(waiting.outcome, NeedsInput)

    running = projector.project(ExecutionFacts(admission=admission, runtime_running=True))
    assert running.status is ExecutionStatus.RUNNING
    assert isinstance(running.outcome, Accepted)

    queued = projector.project(ExecutionFacts(admission=_admission()))
    assert queued.status is None
    assert queued.outcome.public_status == {"admission": "START_QUEUED"}


def test_completed_requires_atomic_publication_evidence():
    with pytest.raises(AdmissionContractError, match="delivery_outbox_id"):
        FinalPublicationCommitted("response", {}, "event", "")


@pytest.mark.parametrize(
    ("producer", "outcome_type"),
    [
        (SignalProducer.PRINCIPAL, NeedsInput),
        (SignalProducer.MEDIA_RECEIPT, Accepted),
        (SignalProducer.RECONCILIATION_RECEIPT, Reconciling),
    ],
)
def test_pending_signal_producer_determines_safe_client_capability(
    producer, outcome_type,
):
    signal = PendingSignalView(
        "signal", producer, "kind", "later",
        "publication" if producer is SignalProducer.PRINCIPAL else "",
    )
    view = ExecutionViewProjector().project(ExecutionFacts(
        admission=_admission(AdmissionStatus.EXECUTION_BOUND), pending_signal=signal,
    ))
    assert isinstance(view.outcome, outcome_type)


def test_conflicting_terminal_facts_fail_closed():
    with pytest.raises(ExecutionProjectionConflict, match="conflicting terminal"):
        ExecutionViewProjector().project(ExecutionFacts(
            admission=_admission(AdmissionStatus.EXECUTION_BOUND),
            final_response=FinalPublicationCommitted(
                "response", {}, "event", "outbox",
            ),
            handoff=HandedOff("ticket", "handoff"),
        ))


def test_expired_before_start_never_revives_old_run():
    view = ExecutionViewProjector().project(ExecutionFacts(
        admission=_admission(AdmissionStatus.EXPIRED_BEFORE_START),
    ))
    assert isinstance(view.outcome, Expired)
    assert view.outcome.new_request_required is True


def test_each_compat_runtime_state_has_one_explicit_m3_cutover_mapping():
    assert M3_COMPATIBILITY_CUTOVER == {
        "RUNNING": "ExecutionView.RUNNING",
        "WAITING_APPROVAL": "ExecutionView.WAITING:PendingSignal.PRINCIPAL",
        "COMPLETED": "ExecutionView.COMPLETED:requires_atomic_final_publication",
        "BLOCKED": "ExecutionView.FAILED:reason=BLOCKED",
        "TOOL_ERROR": "ExecutionView.FAILED:reason=TOOL_ERROR",
        "MAX_STEPS": "ExecutionView.FAILED:reason=BUDGET_EXCEEDED",
        "CANCELLED": "ExecutionView.CANCELLED",
        "EXPIRED": "Expired(stage=RUNTIME,new_request_required=true)",
    }
    assert set(M3_COMPATIBILITY_CUTOVER) == {status.name for status in RunStatus}


@pytest.mark.parametrize("terminal", [
    HandedOff("ticket", "handoff"),
    Cancelled("run-1", "user_cancelled"),
    Failed("typed", False, "trace"),
])
def test_each_terminal_fact_has_a_deterministic_projection(terminal):
    key = {
        HandedOff: "handoff",
        Cancelled: "cancellation",
        Failed: "failure",
    }[type(terminal)]
    view = ExecutionViewProjector().project(ExecutionFacts(
        admission=_admission(AdmissionStatus.EXECUTION_BOUND), **{key: terminal},
    ))
    assert view.outcome is terminal
