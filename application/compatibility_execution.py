"""M1 compatibility invocation work-item contract.

This is durable consumption metadata for the opaque compatibility runtime.  It
does not duplicate Agent node state: final publication and typed terminal facts
remain the public execution authorities.
"""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Any, Awaitable, Callable, Mapping, Protocol

from application.chat_application import (
    Cancelled,
    ChatOutcome,
    Completed,
    Conflict,
    Expired,
    Failed,
    HandedOff,
    Rejected,
    StageObservation,
    StageStatus,
)
from core.identity import InvocationKey, WorkflowRunId


class CompatibilityExecutionError(RuntimeError):
    pass


class CompatibilityExecutionClaimLost(CompatibilityExecutionError):
    pass


@dataclass(frozen=True)
class CompatibilityExecutionItem:
    job_id: str
    invocation_key: InvocationKey
    workflow_run_id: WorkflowRunId
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
    lease_until: str

    def __post_init__(self) -> None:
        required = (
            self.job_id, str(self.invocation_key), str(self.workflow_run_id),
            self.tenant_id, self.user_id, self.conversation_id,
            self.request_id, self.continuation_id, self.message,
            self.claimed_by, self.lease_until,
        )
        if any(not str(value).strip() for value in required):
            raise CompatibilityExecutionError("compatibility execution item is incomplete")
        if self.deletion_epoch < 0 or self.attempt < 1:
            raise CompatibilityExecutionError("compatibility claim epoch is invalid")
        if any(not str(key).strip() or not str(value).strip() for key, value in self.pinned_versions.items()):
            raise CompatibilityExecutionError("pinned execution versions are incomplete")


@dataclass(frozen=True)
class CompatibilityExecutionTerminal:
    outcome_type: str
    payload: Mapping[str, Any]
    terminal_ref: str

    def __post_init__(self) -> None:
        if not self.outcome_type.strip() or not self.terminal_ref.strip():
            raise CompatibilityExecutionError("terminal execution fact is incomplete")


class CompatibilityExecutionOutbox(Protocol):
    def claim(
        self, *, worker_id: str, lease_seconds: int, limit: int = 1,
        invocation_key: InvocationKey | None = None,
    ) -> tuple[CompatibilityExecutionItem, ...]: ...

    def renew(
        self, item: CompatibilityExecutionItem, *, worker_id: str,
        lease_seconds: int,
    ) -> None: ...

    def assert_owned(
        self, item: CompatibilityExecutionItem, *, worker_id: str,
    ) -> None: ...

    def acknowledge(
        self, item: CompatibilityExecutionItem, *, worker_id: str,
        terminal: CompatibilityExecutionTerminal,
    ) -> None: ...

    def release(
        self, item: CompatibilityExecutionItem, *, worker_id: str,
        error_code: str, retry_after_seconds: int = 0,
    ) -> None: ...

    def terminal(
        self, invocation_key: InvocationKey,
    ) -> CompatibilityExecutionTerminal | None: ...


ExecutionGuard = Callable[[], Awaitable[None]]
CompatibilityExecutor = Callable[
    [CompatibilityExecutionItem, ExecutionGuard], Awaitable[ChatOutcome]
]


class CompatibilityExecutionWorker:
    """Lease-fenced at-least-once driver for one whole invocation."""

    def __init__(
        self,
        outbox: CompatibilityExecutionOutbox,
        executor: CompatibilityExecutor,
        *,
        lease_seconds: int = 300,
        heartbeat_seconds: float = 30.0,
        retry_after_seconds: int = 1,
    ):
        if lease_seconds < 2 or heartbeat_seconds <= 0:
            raise ValueError("compatibility execution lease/heartbeat is invalid")
        if heartbeat_seconds >= lease_seconds / 2:
            raise ValueError("heartbeat must renew before half the lease")
        self.outbox = outbox
        self.executor = executor
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.retry_after_seconds = max(0, retry_after_seconds)

    async def run_once(
        self, *, worker_id: str,
        invocation_key: InvocationKey | None = None,
    ) -> tuple[ChatOutcome, ...]:
        items = await asyncio.to_thread(
            self.outbox.claim, worker_id=worker_id,
            lease_seconds=self.lease_seconds, limit=1,
            invocation_key=invocation_key,
        )
        outcomes = []
        for item in items:
            stop = asyncio.Event()
            heartbeat = asyncio.create_task(self._heartbeat(item, worker_id, stop))

            async def guard() -> None:
                await asyncio.to_thread(
                    self.outbox.assert_owned, item, worker_id=worker_id,
                )

            try:
                outcome = await self.executor(item, guard)
                await guard()
                if isinstance(outcome, Failed) and outcome.retryable:
                    await asyncio.to_thread(
                        self.outbox.release, item, worker_id=worker_id,
                        error_code=outcome.code,
                        retry_after_seconds=self.retry_after_seconds,
                    )
                else:
                    terminal = terminal_from_outcome(outcome)
                    await asyncio.to_thread(
                        self.outbox.acknowledge, item, worker_id=worker_id,
                        terminal=terminal,
                    )
                outcomes.append(outcome)
            except Exception as exc:
                try:
                    await asyncio.to_thread(
                        self.outbox.release, item, worker_id=worker_id,
                        error_code=type(exc).__name__,
                        retry_after_seconds=self.retry_after_seconds,
                    )
                except CompatibilityExecutionClaimLost:
                    pass
                raise
            finally:
                stop.set()
                await heartbeat
        return tuple(outcomes)

    async def _heartbeat(
        self, item: CompatibilityExecutionItem, worker_id: str, stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
                return
            except TimeoutError:
                await asyncio.to_thread(
                    self.outbox.renew, item, worker_id=worker_id,
                    lease_seconds=self.lease_seconds,
                )


def terminal_from_outcome(outcome: ChatOutcome) -> CompatibilityExecutionTerminal:
    if isinstance(outcome, Completed):
        return CompatibilityExecutionTerminal(
            "COMPLETED", {
                "response_id": outcome.response_id,
                "response": dict(outcome.response),
                "stages": [item.to_dict() for item in outcome.stages],
            }, outcome.response_id,
        )
    if isinstance(outcome, HandedOff):
        payload = {"ticket_id": outcome.ticket_id, "handoff_id": outcome.handoff_id}
        return CompatibilityExecutionTerminal(
            "HANDED_OFF", payload, outcome.handoff_id,
        )
    if isinstance(outcome, Cancelled):
        payload = {
            "workflow_run_id": outcome.workflow_run_id,
            "reason_code": outcome.reason_code,
        }
        return CompatibilityExecutionTerminal(
            "CANCELLED", payload, outcome.workflow_run_id,
        )
    if isinstance(outcome, Expired):
        payload = {
            "workflow_run_id": outcome.workflow_run_id, "stage": outcome.stage,
            "new_request_required": outcome.new_request_required,
        }
        return CompatibilityExecutionTerminal(
            "EXPIRED", payload, outcome.workflow_run_id,
        )
    if isinstance(outcome, Rejected):
        payload = {"code": outcome.code, "safe_message": outcome.safe_message}
        return CompatibilityExecutionTerminal("REJECTED", payload, outcome.code)
    if isinstance(outcome, Conflict):
        payload = {
            "code": outcome.code,
            "existing_invocation": dict(outcome.existing_invocation),
        }
        return CompatibilityExecutionTerminal("CONFLICT", payload, outcome.code)
    if isinstance(outcome, Failed) and not outcome.retryable:
        payload = {
            "code": outcome.code, "retryable": False,
            "correlation_id": outcome.correlation_id,
            "safe_message": outcome.safe_message,
            "stages": [item.to_dict() for item in outcome.stages],
        }
        return CompatibilityExecutionTerminal("FAILED", payload, outcome.code)
    raise CompatibilityExecutionError(
        f"compatibility executor returned non-terminal {type(outcome).__name__}"
    )


def outcome_from_terminal(terminal: CompatibilityExecutionTerminal) -> ChatOutcome:
    value = dict(terminal.payload)
    kind = terminal.outcome_type
    if kind == "COMPLETED":
        stages = tuple(StageObservation(
            stage=str(item["stage"]), status=StageStatus(item["status"]),
            detail=dict(item["detail"]),
        ) for item in value.get("stages", ()))
        return Completed(str(value["response_id"]), dict(value["response"]), stages)
    if kind == "HANDED_OFF":
        return HandedOff(str(value["ticket_id"]), str(value["handoff_id"]))
    if kind == "CANCELLED":
        return Cancelled(str(value["workflow_run_id"]), str(value["reason_code"]))
    if kind == "EXPIRED":
        return Expired(
            str(value["workflow_run_id"]), str(value["stage"]),
            bool(value.get("new_request_required", True)),
        )
    if kind == "REJECTED":
        return Rejected(str(value["code"]), str(value["safe_message"]))
    if kind == "CONFLICT":
        return Conflict(str(value["code"]), dict(value["existing_invocation"]))
    if kind == "FAILED":
        stages = tuple(StageObservation(
            stage=str(item["stage"]), status=StageStatus(item["status"]),
            detail=dict(item["detail"]),
        ) for item in value.get("stages", ()))
        return Failed(
            str(value["code"]), False, str(value["correlation_id"]),
            str(value.get("safe_message") or ""), stages,
        )
    raise CompatibilityExecutionError(f"unknown terminal outcome {kind!r}")
