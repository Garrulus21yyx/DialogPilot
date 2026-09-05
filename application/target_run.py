"""Durable background ownership for admitted Target chat invocations."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol

from application.admission_contract import AdmissionConflict, ClaimStart
from application.chat_contracts import Accepted, Cancelled, ChatCommand, ChatOutcome, Completed, Conflict, Expired, Failed, HandedOff, NeedsInput, Reconciling, Rejected
from application.target_chat_application import (
    TargetAdmissionStatus,
    TargetChatApplication,
)
from core.identity import IdentityContractError, InvocationKey, WorkflowRunId


class TargetRunClaimLost(RuntimeError):
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


@dataclass(frozen=True)
class TargetRunTerminal:
    status: str
    payload: Mapping[str, Any]
    terminal_ref: str


class TargetRunStore(Protocol):
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

    def release(
        self, item: TargetRunItem, *, worker_id: str, error_code: str,
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
    ) -> None:
        if lease_seconds < 2 or heartbeat_seconds <= 0:
            raise ValueError("target run lease and heartbeat must be positive")
        if heartbeat_seconds >= lease_seconds / 2:
            raise ValueError("target run heartbeat must precede half the lease")
        self._store = store
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._retry_after_seconds = max(0, retry_after_seconds)

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
            stop = asyncio.Event()
            heartbeat = asyncio.create_task(self._heartbeat(item, worker_id, stop))

            async def guard() -> None:
                await asyncio.to_thread(
                    self._store.assert_owned, item, worker_id=worker_id,
                )

            try:
                outcome = await self._executor(item, guard)
                await guard()
                if isinstance(outcome, Failed) and outcome.retryable:
                    await asyncio.to_thread(
                        self._store.release,
                        item,
                        worker_id=worker_id,
                        error_code=outcome.code,
                        retry_after_seconds=self._retry_after_seconds,
                    )
                else:
                    await asyncio.to_thread(
                        self._store.complete,
                        item,
                        worker_id=worker_id,
                        terminal=terminal_from_outcome(outcome),
                    )
                outcomes.append(outcome)
            except Exception as exc:
                try:
                    await asyncio.to_thread(
                        self._store.release,
                        item,
                        worker_id=worker_id,
                        error_code=type(exc).__name__,
                        retry_after_seconds=self._retry_after_seconds,
                    )
                except TargetRunClaimLost:
                    pass
                raise
            finally:
                stop.set()
                await heartbeat
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


def terminal_from_outcome(outcome: ChatOutcome) -> TargetRunTerminal:
    if isinstance(outcome, Completed):
        return TargetRunTerminal(
            "COMPLETED", {
                "response_id": outcome.response_id,
                "response": dict(outcome.response),
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
        }, outcome.interaction_publication_id)
    if isinstance(outcome, Reconciling):
        return TargetRunTerminal("RECONCILING", {
            "workflow_run_id": outcome.workflow_run_id,
            "public_status": dict(outcome.public_status),
            "next_poll_after": outcome.next_poll_after,
        }, outcome.workflow_run_id)
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
        return TargetRunTerminal("FAILED", {
            "code": outcome.code,
            "retryable": False,
            "correlation_id": outcome.correlation_id,
            "safe_message": outcome.safe_message,
        }, outcome.code)
    raise ValueError(f"non-terminal target outcome: {type(outcome).__name__}")


def outcome_from_terminal(terminal: TargetRunTerminal) -> ChatOutcome:
    value = dict(terminal.payload)
    if terminal.status == "COMPLETED":
        return Completed(str(value["response_id"]), dict(value["response"]))
    if terminal.status in {"WAITING_INPUT", "WAITING_APPROVAL"}:
        return NeedsInput(
            str(value["workflow_run_id"]), str(value["signal_id"]),
            str(value["kind"]), str(value["expires_at"]),
            str(value["interaction_publication_id"]),
        )
    if terminal.status == "RECONCILING":
        return Reconciling(
            str(value["workflow_run_id"]), dict(value["public_status"]),
            float(value["next_poll_after"]),
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
        )
    raise ValueError(f"unknown target run terminal status {terminal.status!r}")
