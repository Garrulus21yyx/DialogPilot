"""Durable background ownership for admitted Target chat invocations."""
from __future__ import annotations

import asyncio
import json
import os
import logging
from langgraph.errors import GraphBubbleUp
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol

from application.admission_contract import AdmissionConflict, ClaimStart
from application.chat_contracts import StageObservation, StageStatus
from application.chat_contracts import Accepted, Cancelled, ChatCommand, ChatOutcome, Completed, Conflict, Expired, Failed, HandedOff, NeedsInput, Reconciling, Rejected
from application.target_chat_application import (
    TargetAdmissionStatus,
    TargetChatApplication,
)
from core.identity import IdentityContractError, InvocationKey, WorkflowRunId
from core.capacity_metrics import decisions, wait_seconds


class TargetRunClaimLost(GraphBubbleUp):
    pass


@dataclass(frozen=True)
class TargetRunItem:
    run_id: WorkflowRunId
    invocation_key: InvocationKey
    tenant_id: str
    user_id: str
    conversation_id: str
    request_id: str
    continuation_id: str
    message: str
    asset_ids: tuple[str, ...]
    pinned_versions: Mapping[str, str]
    deletion_epoch: int
    attempt: int
    claimed_by: str
    admitted_at: datetime
    selected_failure: Failed | None = None
    failure_count: int = 0
    execution_started_at: datetime | None = None


@dataclass(frozen=True)
class TargetRunTerminal:
    status: str
    payload: Mapping[str, Any]
    terminal_ref: str


class TargetRunStore(Protocol):
    def acquire_execution(self, item: TargetRunItem, *, worker_id: str) -> bool: ...

    def defer(self, item: TargetRunItem, *, worker_id: str) -> None: ...

    def claim(
        self, *, worker_id: str, lease_seconds: int, limit: int = 1,
        invocation_key: InvocationKey | None = None,
    ) -> tuple[TargetRunItem, ...]: ...

    def renew(
        self, item: TargetRunItem, *, worker_id: str, lease_seconds: int,
    ) -> None: ...

    def assert_owned(self, item: TargetRunItem, *, worker_id: str) -> None: ...

    def complete(
        self, item: TargetRunItem, *, worker_id: str,
        terminal: TargetRunTerminal,
    ) -> None: ...

    def select_failure(self, item: TargetRunItem, *, worker_id: str, failure: Failed) -> None: ...

    def release(
        self, item: TargetRunItem, *, worker_id: str, failure: Failed,
        retry_after_seconds: int = 0,
    ) -> None: ...

    def terminal(
        self, invocation_key: InvocationKey,
    ) -> TargetRunTerminal | None: ...


ExecutionGuard = Callable[[], Awaitable[None]]


class TargetRunWorker:
    """Lease-fenced owner of planning, execution and publication for one turn."""

    def __init__(
        self,
        store: TargetRunStore,
        executor: Callable[[TargetRunItem, ExecutionGuard], Awaitable[ChatOutcome]],
        *,
        lease_seconds: int = 300,
        heartbeat_seconds: float = 30.0,
        retry_after_seconds: int = 1,
        max_attempts: int = 3,
        max_queue_wait_seconds: float | None = None,
        finalize_failure: Callable[[TargetRunItem, Failed], Awaitable[ChatOutcome]] | None = None,
    ) -> None:
        if lease_seconds < 2 or heartbeat_seconds <= 0:
            raise ValueError("target run lease and heartbeat must be positive")
        if heartbeat_seconds >= lease_seconds / 2:
            raise ValueError("target run heartbeat must precede half the lease")
        if max_attempts < 1:
            raise ValueError("target run attempt budget must be positive")
        self._store = store
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._retry_after_seconds = max(0, retry_after_seconds)
        self._max_attempts = max_attempts
        self._max_queue_wait_seconds = (float(os.getenv("TARGET_MAX_QUEUE_WAIT_SECONDS", "300"))
            if max_queue_wait_seconds is None else max_queue_wait_seconds)
        if self._max_queue_wait_seconds <= 0:
            raise ValueError("queue waiting deadline must be positive")
        self._finalize_failure = finalize_failure

    async def run_once(
        self, *, worker_id: str,
        invocation_key: InvocationKey | None = None,
    ) -> tuple[ChatOutcome, ...]:
        items = await asyncio.to_thread(
            self._store.claim,
            worker_id=worker_id,
            lease_seconds=self._lease_seconds,
            limit=1,
            invocation_key=invocation_key,
        )
        outcomes: list[ChatOutcome] = []
        for item in items:
            from application.run_execution import bind_run_execution, ExecutionDeferred
            ownership = bind_run_execution(self._store, item, worker_id)
            ownership.__enter__()
            if item.attempt == 1:
                wait_seconds.labels("target_queue").observe(max(0,
                    (datetime.now(timezone.utc) - item.admitted_at).total_seconds()))
            stop = asyncio.Event()
            heartbeat = asyncio.create_task(self._heartbeat(item, worker_id, stop))

            async def guard() -> None:
                await asyncio.to_thread(
                    self._store.assert_owned, item, worker_id=worker_id,
                )

            try:
                try:
                    if item.selected_failure is not None:
                        outcome = item.selected_failure
                    elif item.execution_started_at is None and (
                        datetime.now(timezone.utc) - item.admitted_at
                    ).total_seconds() > self._max_queue_wait_seconds:
                        # Only work that has never started expires here. A
                        # recovery attempt may own an already committed write.
                        outcome = Failed("RUN_QUEUE_EXPIRED", False,
                            str(item.invocation_key), "The request waited too long to start. No business action was started.",
                            stages=(StageObservation("run_queue", StageStatus.FAILED,
                                {"code": "RUN_QUEUE_EXPIRED"}),))
                        decisions.labels("target_queue", "expired").inc()
                    elif item.failure_count >= self._max_attempts:
                        outcome = Failed("run_attempt_budget_exhausted", False,
                            str(item.invocation_key), "Execution stopped; saved operation records require review.",
                            stages=(StageObservation("run_retry", StageStatus.FAILED, {
                                "code": "RUN_ATTEMPT_BUDGET_EXHAUSTED", "attempt": item.attempt,
                                "max_attempts": self._max_attempts,
                            }),))
                    else:
                        outcome = await self._executor(item, guard)
                except (TargetRunClaimLost, GraphBubbleUp):
                    raise
                except Exception as exc:
                    from core.tracing import exception_chain
                    outcome = Failed("unclassified_execution_failure", False,
                        str(item.invocation_key),
                        "The run stopped unexpectedly. Existing operation results need to be checked.",
                        stages=(StageObservation("run_execution", StageStatus.FAILED, {
                            "code": type(exc).__name__, "exception_chain": exception_chain(exc),
                        }),))
                await guard()
                if isinstance(outcome, Failed) and outcome.retryable and item.failure_count + 1 >= self._max_attempts:
                    outcome = replace(outcome, retryable=False, stages=(*outcome.stages,
                        StageObservation("run_retry", StageStatus.FAILED, {
                            "code": "RUN_ATTEMPT_BUDGET_EXHAUSTED", "attempt": item.attempt,
                            "max_attempts": self._max_attempts,
                        })))
                if isinstance(outcome, Failed) and outcome.retryable:
                    stop.set()
                    await heartbeat
                    await asyncio.to_thread(
                        self._store.release,
                        item,
                        worker_id=worker_id,
                        failure=outcome,
                        retry_after_seconds=self._retry_after_seconds,
                    )
                else:
                    if isinstance(outcome, Failed) and item.selected_failure is None:
                        await asyncio.to_thread(self._store.select_failure,
                            item, worker_id=worker_id, failure=outcome)
                    if isinstance(outcome, Failed) and self._finalize_failure is not None:
                        from application.run_execution import acquire_execution
                        await acquire_execution()
                        outcome = await self._finalize_failure(item, outcome)
                        await guard()
                    stop.set()
                    await heartbeat
                    await asyncio.to_thread(
                        self._store.complete,
                        item,
                        worker_id=worker_id,
                        terminal=terminal_from_outcome(outcome),
                    )
                outcomes.append(outcome)
            except ExecutionDeferred:
                stop.set()
                await heartbeat
                await asyncio.to_thread(self._store.defer, item, worker_id=worker_id)
            finally:
                stop.set()
                try:
                    await heartbeat
                finally:
                    ownership.__exit__(None, None, None)
        return tuple(outcomes)

    async def _heartbeat(
        self, item: TargetRunItem, worker_id: str, stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self._heartbeat_seconds,
                )
                return
            except TimeoutError:
                await asyncio.to_thread(
                    self._store.renew,
                    item,
                    worker_id=worker_id,
                    lease_seconds=self._lease_seconds,
                )


class TargetRunCoordinator:
    """Submit Target turns durably and execute them outside the HTTP request."""

    def __init__(
        self,
        application: TargetChatApplication,
        *,
        dispatcher: Any,
        store: TargetRunStore,
        worker_id: str = "target-run",
        start_lease_seconds: int = 30,
        execution_lease_seconds: int = 300,
        heartbeat_seconds: float = 30.0,
        max_attempts: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.application = application
        self.dispatcher = dispatcher
        self.store = store
        self.worker_id = worker_id
        self.start_lease_seconds = start_lease_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.worker = TargetRunWorker(
            store,
            self._execute,
            lease_seconds=execution_lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
            max_attempts=max_attempts,
            finalize_failure=self._finalize_failure,
        )

    async def handle(self, command: ChatCommand) -> ChatOutcome:
        try:
            identity = self.application.identity_for(command)
        except IdentityContractError:
            return Rejected("invalid_invocation_identity", "请求身份字段无效")
        admission = await asyncio.to_thread(
            self.application.admit, command, identity,
        )
        if admission.status is TargetAdmissionStatus.CONFLICT:
            return Conflict("IDEMPOTENCY_CONFLICT", admission.existing or {})
        replay = await asyncio.to_thread(self.application.completed, identity)
        if replay is not None:
            return replay
        terminal = await asyncio.to_thread(
            self.store.terminal, identity.invocation_key,
        )
        if terminal is not None:
            return outcome_from_terminal(terminal)
        return Accepted(str(identity.workflow_run_id), {
            "invocation_key": str(identity.invocation_key),
            "admission": admission.status.value,
            "durable_execution": "QUEUED",
        })

    async def await_outcome(
        self, accepted: Accepted, *, timeout_seconds: float = 120,
        poll_seconds: float = 0.1,
    ) -> ChatOutcome:
        """Read the original run's terminal fact; waiting never starts execution."""
        invocation_key = InvocationKey(str(accepted.public_status["invocation_key"]))
        async with asyncio.timeout(timeout_seconds):
            while True:
                terminal = await asyncio.to_thread(self.store.terminal, invocation_key)
                if terminal is not None:
                    return outcome_from_terminal(terminal)
                await asyncio.sleep(poll_seconds)

    async def pump_once(self) -> int:
        now = self.clock()
        dispatched = await asyncio.to_thread(
            self.dispatcher.dispatch_once,
            ClaimStart(
                worker_id=f"{self.worker_id}-starter",
                now=now.isoformat(),
                lease_until=(
                    now + timedelta(seconds=self.start_lease_seconds)
                ).isoformat(),
                limit=100,
                runtime_kind="target",
            ),
        )
        outcomes = await self.worker.run_once(worker_id=self.worker_id)
        return len(dispatched) + len(outcomes)

    async def serve(self, stop: asyncio.Event, *, concurrency: int, poll_seconds: float):
        """Bounded existing Run workers; a long execution does not block admission/planning."""
        if concurrency < 2 or poll_seconds <= 0:
            raise ValueError("dynamic turns need at least two worker slots and a positive poll interval")

        async def consume():
            while not stop.is_set():
                try:
                    count = await self.pump_once()
                except TargetRunClaimLost:
                    count = 0
                except Exception:
                    logging.getLogger(__name__).exception("durable Run worker failed")
                    count = 0
                if not count:
                    try:
                        await asyncio.wait_for(stop.wait(), poll_seconds)
                    except TimeoutError:
                        pass

        async with asyncio.TaskGroup() as group:
            for _ in range(concurrency):
                group.create_task(consume())

    async def _execute(
        self, item: TargetRunItem, guard: ExecutionGuard,
    ) -> ChatOutcome:
        identity = self.application.identity_for(_command_from_item(item))
        if (
            identity.invocation_key != item.invocation_key
            or identity.workflow_run_id != item.run_id
        ):
            raise RuntimeError("target run identity cannot be reconstructed")
        completed = await asyncio.to_thread(self.application.completed, identity)
        if completed is not None:
            return completed
        await guard()
        outcome = await self.application.execute_admitted(
            _command_from_item(item), identity,
        )
        completed = await asyncio.to_thread(self.application.completed, identity)
        return completed or outcome

    async def _finalize_failure(self, item: TargetRunItem, failure: Failed) -> ChatOutcome:
        identity = self.application.identity_for(_command_from_item(item))
        # Publication may already have committed even if its acknowledgement
        # failed. Reuse that fact before creating any failure response.
        completed = await asyncio.to_thread(self.application.completed, identity)
        if completed is not None:
            return completed
        return await asyncio.to_thread(self.application.publish_failure, identity, failure)


def _command_from_item(item: TargetRunItem) -> ChatCommand:
    pins = item.pinned_versions
    approval = pins.get("approval_decision", "none")
    raw_values = json.loads(pins.get("interaction_values", "[]"))
    return ChatCommand(
        message=item.message,
        user_id=item.user_id,
        tenant_id=item.tenant_id,
        conv_id=item.conversation_id,
        request_id=item.request_id,
        continuation_id=item.continuation_id,
        authorization_fingerprint=pins["authorization_fingerprint"],
        asset_ids=item.asset_ids,
        approval_id=pins.get("approval_id") or None,
        approval_decision=(
            True if approval == "approved"
            else False if approval == "declined" else None
        ),
        interaction_id=pins.get("interaction_id") or None,
        interaction_version=(
            int(pins["interaction_version"])
            if pins.get("interaction_version") else None
        ),
        interaction_values=tuple(
            (str(value[0]), str(value[1]), value[2]) for value in raw_values
        ),
    )


def failure_payload(outcome: Failed) -> dict[str, Any]:
    """The same failure record is used for retry evidence and terminal replay."""
    return {"code": outcome.code, "retryable": outcome.retryable,
            "correlation_id": outcome.correlation_id, "safe_message": outcome.safe_message,
            "stages": [stage.to_dict() for stage in outcome.stages], "response_id": outcome.response_id}


def terminal_from_outcome(outcome: ChatOutcome) -> TargetRunTerminal:
    if isinstance(outcome, Completed):
        return TargetRunTerminal(
            "COMPLETED", {
                "response_id": outcome.response_id,
                "response": dict(outcome.response),
                "stages": [stage.to_dict() for stage in outcome.stages],
            }, outcome.response_id,
        )
    if isinstance(outcome, NeedsInput):
        status = "WAITING_APPROVAL" if outcome.kind == "APPROVAL" else "WAITING_INPUT"
        return TargetRunTerminal(status, {
            "workflow_run_id": outcome.workflow_run_id,
            "signal_id": outcome.signal_id,
            "kind": outcome.kind,
            "expires_at": outcome.expires_at,
            "interaction_publication_id": outcome.interaction_publication_id,
            "stages": [stage.to_dict() for stage in outcome.stages],
        }, outcome.interaction_publication_id)
    if isinstance(outcome, Reconciling):
        return TargetRunTerminal("RECONCILING", {
            "workflow_run_id": outcome.workflow_run_id,
            "public_status": dict(outcome.public_status),
            "next_poll_after": outcome.next_poll_after,
            "stages": [stage.to_dict() for stage in outcome.stages],
        }, str(outcome.public_status.get("response_id") or outcome.workflow_run_id))
    if isinstance(outcome, HandedOff):
        return TargetRunTerminal("HANDED_OFF", {
            "ticket_id": outcome.ticket_id, "handoff_id": outcome.handoff_id,
        }, outcome.handoff_id)
    if isinstance(outcome, Cancelled):
        return TargetRunTerminal("CANCELLED", {
            "workflow_run_id": outcome.workflow_run_id,
            "reason_code": outcome.reason_code,
        }, outcome.workflow_run_id)
    if isinstance(outcome, Expired):
        return TargetRunTerminal("EXPIRED", {
            "workflow_run_id": outcome.workflow_run_id,
            "stage": outcome.stage,
            "new_request_required": outcome.new_request_required,
        }, outcome.workflow_run_id)
    if isinstance(outcome, Rejected):
        return TargetRunTerminal("REJECTED", {
            "code": outcome.code, "safe_message": outcome.safe_message,
        }, outcome.code)
    if isinstance(outcome, Conflict):
        return TargetRunTerminal("CONFLICT", {
            "code": outcome.code,
            "existing_invocation": dict(outcome.existing_invocation),
        }, outcome.code)
    if isinstance(outcome, Failed) and not outcome.retryable:
        return TargetRunTerminal("FAILED", failure_payload(outcome), outcome.code)
    raise ValueError(f"non-terminal target outcome: {type(outcome).__name__}")


def outcome_from_terminal(terminal: TargetRunTerminal) -> ChatOutcome:
    value = dict(terminal.payload)
    stages = tuple(StageObservation(item['stage'], StageStatus(item['status']), item['detail'])
                   for item in value.get('stages', ()))
    if terminal.status == "COMPLETED":
        return Completed(str(value["response_id"]), dict(value["response"]), stages=stages)
    if terminal.status in {"WAITING_INPUT", "WAITING_APPROVAL"}:
        return NeedsInput(
            str(value["workflow_run_id"]), str(value["signal_id"]),
            str(value["kind"]), str(value["expires_at"]),
            str(value["interaction_publication_id"]),
            stages=stages,
        )
    if terminal.status == "RECONCILING":
        return Reconciling(
            str(value["workflow_run_id"]), dict(value["public_status"]),
            float(value["next_poll_after"]),
            stages=stages,
        )
    if terminal.status == "HANDED_OFF":
        return HandedOff(str(value["ticket_id"]), str(value["handoff_id"]))
    if terminal.status == "CANCELLED":
        return Cancelled(str(value["workflow_run_id"]), str(value["reason_code"]))
    if terminal.status == "EXPIRED":
        return Expired(
            str(value["workflow_run_id"]), str(value["stage"]),
            bool(value.get("new_request_required", True)),
        )
    if terminal.status == "REJECTED":
        return Rejected(str(value["code"]), str(value["safe_message"]))
    if terminal.status == "CONFLICT":
        return Conflict(str(value["code"]), dict(value["existing_invocation"]))
    if terminal.status == "FAILED":
        return Failed(
            str(value["code"]), False, str(value["correlation_id"]),
            str(value.get("safe_message") or ""),
            stages=stages,
            response_id=value.get("response_id"),
        )
    raise ValueError(f"unknown target run terminal status {terminal.status!r}")
