"""Durable online composition for the legacy whole-invocation executor."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from application.admission_contract import (
    AdmissionConflict,
    ClaimStart,
)
from application.chat_application import ChatApplication
from application.chat_contracts import Accepted, ChatCommand, ChatOutcome, Conflict, Failed
from application.compatibility_execution import (
    CompatibilityExecutionItem,
    CompatibilityExecutionWorker,
    ExecutionGuard,
    outcome_from_terminal,
)
from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory, InvocationKey
from services.evolution import ActiveBundleAssignment, PinnedExecutionRefs


_PIN_FIELDS = tuple(PinnedExecutionRefs.__dataclass_fields__)


class CompatibilityChatCoordinator:
    """Admission-first facade; no LLM/tool work occurs before durable binding."""

    def __init__(
        self,
        application: ChatApplication,
        *,
        admission: Any,
        dispatcher: Any,
        execution_outbox: Any,
        bundle_resolver: Any,
        bundle_registry: Any,
        completed_reader: Any,
        identity_factory: IdentityFactory | None = None,
        worker_id: str = "compat-online",
        start_lease_seconds: int = 30,
        execution_lease_seconds: int = 300,
        heartbeat_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ):
        self.application = application
        self.admission = admission
        self.dispatcher = dispatcher
        self.execution_outbox = execution_outbox
        self.bundle_resolver = bundle_resolver
        self.bundle_registry = bundle_registry
        self.completed_reader = completed_reader
        self.identity_factory = identity_factory or IdentityFactory()
        self.worker_id = worker_id
        self.start_lease_seconds = start_lease_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.worker = CompatibilityExecutionWorker(
            execution_outbox,
            self._execute,
            lease_seconds=execution_lease_seconds,
            heartbeat_seconds=heartbeat_seconds,
        )

    async def handle(self, command: ChatCommand) -> ChatOutcome:
        try:
            identity = self.identity_factory.create_invocation(
                tenant_id=command.tenant_id,
                user_id=command.user_id,
                conversation_id=command.conv_id,
                request_id=command.request_id,
                continuation_id=command.continuation_id,
            )
            assignment = await asyncio.to_thread(
                self.bundle_resolver.resolve, str(identity.user_id),
            )
            pins = assignment_to_pins(
                assignment,
                authorization_fingerprint=command.authorization_fingerprint,
            )
            now = self.clock()
            admitted = await asyncio.to_thread(
                self.admission.admit_new,
                NewInvocationInbound(
                    identity=identity,
                    message=command.message,
                    pinned_versions=pins,
                    created_at=now.isoformat(),
                    asset_ids=command.asset_ids,
                ),
            )
            if isinstance(admitted, AdmissionConflict):
                return Conflict(
                    admitted.code,
                    _record_projection(admitted.existing),
                )

            replay = await self._terminal_or_publication(identity.invocation_key, command.user_id)
            if replay is not None:
                return replay

            await asyncio.to_thread(self._dispatch, now)
            outcomes = await self.worker.run_once(
                worker_id=self.worker_id,
                invocation_key=identity.invocation_key,
            )
            if outcomes:
                return outcomes[0]
            replay = await self._terminal_or_publication(identity.invocation_key, command.user_id)
            if replay is not None:
                return replay
            record = admitted.record
            return Accepted(str(record.workflow_run_id), {
                "admission": record.status.value,
                "durable_execution": "QUEUED",
            })
        except Exception as exc:
            return Failed(
                code="durable_admission_unavailable",
                retryable=True,
                correlation_id=type(exc).__name__,
                safe_message="请求已安全停止；请使用相同 request_id 重试。",
            )

    async def pump_once(self) -> int:
        """Recover abandoned start and execution work across API restarts."""
        now = self.clock()
        dispatched = await asyncio.to_thread(self._dispatch, now)
        outcomes = await self.worker.run_once(worker_id=self.worker_id)
        return len(dispatched) + len(outcomes)

    async def _execute(
        self, item: CompatibilityExecutionItem, guard: ExecutionGuard,
    ) -> ChatOutcome:
        completed = await asyncio.to_thread(
            self.completed_reader.completed_for_invocation,
            item.invocation_key,
            user_id=item.user_id,
        )
        if completed is not None:
            return completed
        assignment = assignment_from_pins(item.pinned_versions, self.bundle_registry)
        identity = self.identity_factory.create_invocation(
            tenant_id=item.tenant_id,
            user_id=item.user_id,
            conversation_id=item.conversation_id,
            request_id=item.request_id,
            continuation_id=item.continuation_id,
        )
        if (
            identity.invocation_key != item.invocation_key
            or identity.workflow_run_id != item.workflow_run_id
        ):
            raise RuntimeError("durable invocation identity cannot be reconstructed")
        command = ChatCommand(
            message=item.message,
            user_id=item.user_id,
            tenant_id=item.tenant_id,
            conv_id=item.conversation_id,
            request_id=item.request_id,
            continuation_id=item.continuation_id,
            authorization_fingerprint=item.pinned_versions["authorization_fingerprint"],
            asset_ids=item.asset_ids,
        )
        outcome = await self.application.execute_pinned(
            command, identity, assignment=assignment, publication_guard=guard,
        )
        # Publication is the completion authority.  A later projection or
        # telemetry failure must never overwrite it with a compatibility error.
        completed = await asyncio.to_thread(
            self.completed_reader.completed_for_invocation,
            item.invocation_key,
            user_id=item.user_id,
        )
        return completed or outcome

    async def _terminal_or_publication(
        self, invocation_key: InvocationKey, user_id: str,
    ) -> ChatOutcome | None:
        completed = await asyncio.to_thread(
            self.completed_reader.completed_for_invocation,
            invocation_key,
            user_id=user_id,
        )
        if completed is not None:
            return completed
        terminal = await asyncio.to_thread(
            self.execution_outbox.terminal, invocation_key,
        )
        return outcome_from_terminal(terminal) if terminal is not None else None

    def _dispatch(self, now: datetime):
        return self.dispatcher.dispatch_once(ClaimStart(
            worker_id=f"{self.worker_id}-starter",
            now=now.isoformat(),
            lease_until=(now + timedelta(seconds=self.start_lease_seconds)).isoformat(),
            limit=100,
        ))


def assignment_to_pins(
    assignment: ActiveBundleAssignment, *, authorization_fingerprint: str,
) -> dict[str, str]:
    if assignment.pinned_refs is None:
        raise ValueError("durable admission requires complete execution refs")
    auth = str(authorization_fingerprint).strip()
    if not auth:
        raise ValueError("durable admission requires authorization fingerprint")
    result = {"authorization_fingerprint": auth}
    result.update({f"primary_{name}": str(getattr(assignment.pinned_refs, name)) for name in _PIN_FIELDS})
    return result


def assignment_from_pins(
    pins: Mapping[str, str], bundle_registry: Any,
) -> ActiveBundleAssignment:
    primary_refs = PinnedExecutionRefs(**{
        name: pins[f"primary_{name}"] for name in _PIN_FIELDS
    })
    primary = bundle_registry.get(primary_refs.bundle_version)
    if primary.content_hash != primary_refs.bundle_hash:
        raise ValueError("pinned primary bundle content changed")
    return ActiveBundleAssignment(
        primary=primary,
        pinned_refs=primary_refs,
    )


def _record_projection(record: Any) -> dict[str, Any]:
    return {
        "invocation_key": str(record.invocation_key),
        "workflow_run_id": str(record.workflow_run_id),
        "status": record.status.value,
        "version": record.version,
        "pinned_versions": dict(record.pinned_versions),
    }
