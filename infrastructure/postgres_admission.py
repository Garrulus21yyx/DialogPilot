"""Atomic inbound admission, durable outbox lease and stable run binding."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.admission_contract import (
    AdmissionConflict,
    AdmissionCapacityExceeded,
    AdmissionCreated,
    AdmissionExisting,
    AdmissionRecord,
    AdmissionResult,
    AdmissionStatus,
    CasAlreadyApplied,
    CasApplied,
    ClaimStart,
    ExecutionPointer,
    StartOutboxItem,
    request_fingerprint,
)
from application.conversation_store import content_hash
from application.inbound_admission import (
    InvalidResumeInbound,
    NewInvocationInbound,
    ResumeAdmissionResult,
    ResumeQueued,
    ResumeRejected,
    ResumeRejectionCode,
    ValidResumeInbound,
)
from core.identity import InvocationKey, OperationKey, WorkflowRunId
from infrastructure.postgres import PostgresPool
from infrastructure.postgres_conversation import PostgresInvocationRepository
from core.capacity_metrics import decisions


FaultHook = Callable[[str], None]


class StableRunBinder(Protocol):
    def get_or_create(
        self,
        *,
        invocation_key: InvocationKey,
        workflow_run_id: WorkflowRunId,
        pinned_versions: Mapping[str, str],
    ) -> ExecutionPointer: ...


@dataclass(frozen=True)
class DispatchAttempt:
    outbox_id: OperationKey
    status: str
    run_id: str = ""
    error_type: str = ""


class PostgresAdmissionUnitOfWork:
    def __init__(self, pool: PostgresPool, *, fault_hook: FaultHook | None = None,
                 max_pending: int | None = None):
        self.pool = pool
        self.fault_hook = fault_hook or (lambda _stage: None)
        self.max_pending = max_pending if max_pending is not None else int(os.getenv("TARGET_MAX_PENDING_RUNS", "1000"))
        if self.max_pending < 1:
            raise ValueError("TARGET_MAX_PENDING_RUNS must be positive")

    def admit_synchronous(
        self,
        command: NewInvocationInbound,
        execution_pointer: ExecutionPointer,
    ) -> AdmissionResult:
        """Atomically admit and bind a synchronous Target v1 invocation.

        No compatibility start-outbox row is created: the caller already owns
        execution and must commit a Publication before claiming completion.
        """
        identity = command.identity
        fingerprint = request_fingerprint({
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
            "conversation_id": str(identity.conversation_id),
            "request_id": str(identity.request_id),
            "continuation_id": str(identity.continuation_id),
            "message": command.message,
            "asset_ids": list(command.asset_ids),
            "authorization_fingerprint": str(
                command.pinned_versions.get("authorization_fingerprint") or ""
            ),
            # Target synchronous inputs may carry state-changing approval
            # semantics. All pinned execution inputs therefore participate in
            # idempotency, not only the authorization fingerprint.
            "pinned_versions": dict(command.pinned_versions),
        })
        record = AdmissionRecord(
            identity.invocation_key,
            identity.workflow_run_id,
            fingerprint,
            AdmissionStatus.EXECUTION_BOUND,
            0,
            dict(command.pinned_versions),
            execution_pointer,
        )
        with self.pool.transaction() as connection:
            existing = self._invocation(connection, identity.invocation_key)
            if existing is not None:
                return self._admission_replay(existing, record)
            self._lock_conversation(connection, identity)
            existing = self._invocation(connection, identity.invocation_key)
            if existing is not None:
                return self._admission_replay(existing, record)
            turn_seq = self._next_seq(connection, identity, "next_turn_seq")
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_turns (
                    turn_key,turn_id,tenant_id,user_id,conversation_id,seq,
                    role,content,content_sha256,request_id,invocation_key,
                    metadata,created_at,retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,'inbound',%s,%s,%s,%s,%s,%s,%s)
            """, (
                str(identity.turn_key), str(identity.turn_id),
                str(identity.tenant_id), str(identity.user_id),
                str(identity.conversation_id), turn_seq, command.message,
                content_hash({"role": "inbound", "content": command.message}),
                str(identity.request_id), str(identity.invocation_key),
                Jsonb({**identity.metadata(), "asset_ids": list(command.asset_ids)}),
                command.created_at, command.retention_until,
            ))
            self._advance_seq(connection, identity, "next_turn_seq", command.created_at)

            event_seq = self._next_seq(connection, identity, "next_event_seq")
            accepted_operation = identity.operation_key(
                "Application", "TARGET_REQUEST_ACCEPTED", str(identity.request_id),
            )
            accepted_payload = {
                "inbound_turn_key": str(identity.turn_key),
                "invocation_key": str(identity.invocation_key),
                "workflow_run_id": str(identity.workflow_run_id),
                "runtime_kind": execution_pointer.runtime_kind,
                "runtime_version": execution_pointer.runtime_version,
            }
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_events (
                    event_id,operation_key,tenant_id,user_id,conversation_id,seq,
                    event_type,payload,content_sha256,request_id,invocation_key,
                    created_at,retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,'TARGET_REQUEST_ACCEPTED',%s,%s,%s,%s,%s,%s)
            """, (
                _fact_id("event", str(accepted_operation)), str(accepted_operation),
                str(identity.tenant_id), str(identity.user_id),
                str(identity.conversation_id), event_seq, Jsonb(accepted_payload),
                content_hash({
                    "event_type": "TARGET_REQUEST_ACCEPTED",
                    "payload": accepted_payload,
                }),
                str(identity.request_id), str(identity.invocation_key),
                command.created_at, command.retention_until,
            ))
            self._advance_seq(connection, identity, "next_event_seq", command.created_at)

            connection.execute("""
                INSERT INTO dialogpilot_app.workflow_invocations (
                    invocation_key,tenant_id,user_id,conversation_id,request_id,
                    workflow_run_id,continuation_id,inbound_turn_key,
                    request_fingerprint,admission_status,pinned_versions,
                    execution_runtime_kind,execution_runtime_version,
                    execution_run_id,version,created_at,updated_at,retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'EXECUTION_BOUND',%s,
                          %s,%s,%s,0,%s,%s,%s)
            """, (
                str(identity.invocation_key), str(identity.tenant_id),
                str(identity.user_id), str(identity.conversation_id),
                str(identity.request_id), str(identity.workflow_run_id),
                str(identity.continuation_id), str(identity.turn_key), fingerprint,
                Jsonb(dict(command.pinned_versions)),
                execution_pointer.runtime_kind, execution_pointer.runtime_version,
                execution_pointer.run_id, command.created_at, command.created_at,
                command.retention_until,
            ))
            return AdmissionCreated(record)

    def admit_new(self, command: NewInvocationInbound) -> AdmissionResult:
        identity = command.identity
        fingerprint = request_fingerprint({
            "tenant_id": str(identity.tenant_id),
            "user_id": str(identity.user_id),
            "conversation_id": str(identity.conversation_id),
            "request_id": str(identity.request_id),
            "continuation_id": str(identity.continuation_id),
            "message": command.message,
            "asset_ids": list(command.asset_ids),
            "authorization_fingerprint": str(
                command.pinned_versions.get("authorization_fingerprint") or ""
            ),
            "pinned_versions": dict(command.pinned_versions),
        })
        record = AdmissionRecord(
            invocation_key=identity.invocation_key,
            workflow_run_id=identity.workflow_run_id,
            request_fingerprint=fingerprint,
            status=AdmissionStatus.START_QUEUED,
            version=0,
            pinned_versions=dict(command.pinned_versions),
        )
        with self.pool.transaction() as connection:
            existing = self._invocation(connection, identity.invocation_key)
            if existing is not None:
                return self._admission_replay(existing, record)
            # All new durable Target turns (including user replies) use this
            # boundary. Serialize capacity decisions, not business execution.
            # Existing request identities remain replayable at capacity.
            if command.runtime_kind == "target":
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (754208190321,))
                existing = self._invocation(connection, identity.invocation_key)
                if existing is not None:
                    return self._admission_replay(existing, record)
                self._assert_capacity(connection)
            self._lock_conversation(connection, identity)
            existing = self._invocation(connection, identity.invocation_key)
            if existing is not None:
                return self._admission_replay(existing, record)
            turn_seq = self._next_seq(connection, identity, "next_turn_seq")
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_turns (
                    turn_key, turn_id, tenant_id, user_id, conversation_id,
                    seq, role, content, content_sha256, request_id,
                    invocation_key, metadata, created_at, retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,'inbound',%s,%s,%s,%s,%s,%s,%s)
            """, (
                str(identity.turn_key), str(identity.turn_id),
                str(identity.tenant_id), str(identity.user_id),
                str(identity.conversation_id), turn_seq, command.message,
                content_hash({"role": "inbound", "content": command.message}),
                str(identity.request_id), str(identity.invocation_key),
                Jsonb({
                    **identity.metadata(),
                    "asset_ids": list(command.asset_ids),
                }), command.created_at, command.retention_until,
            ))
            self._advance_seq(connection, identity, "next_turn_seq", command.created_at)
            self.fault_hook("after_inbound")

            event_seq = self._next_seq(connection, identity, "next_event_seq")
            accepted_operation = identity.operation_key(
                "Application", "REQUEST_ACCEPTED", str(identity.request_id),
            )
            accepted_payload = {
                "invocation_key": str(identity.invocation_key),
                "workflow_run_id": str(identity.workflow_run_id),
                "inbound_turn_key": str(identity.turn_key),
            }
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_events (
                    event_id, operation_key, tenant_id, user_id, conversation_id,
                    seq, event_type, payload, content_sha256, request_id,
                    invocation_key, created_at, retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,'REQUEST_ACCEPTED',%s,%s,%s,%s,%s,%s)
            """, (
                _fact_id("event", str(accepted_operation)), str(accepted_operation),
                str(identity.tenant_id), str(identity.user_id),
                str(identity.conversation_id), event_seq, Jsonb(accepted_payload),
                content_hash({
                    "event_type": "REQUEST_ACCEPTED", "payload": accepted_payload,
                }), str(identity.request_id), str(identity.invocation_key),
                command.created_at, command.retention_until,
            ))
            self._advance_seq(connection, identity, "next_event_seq", command.created_at)
            self.fault_hook("after_accepted_event")

            connection.execute("""
                INSERT INTO dialogpilot_app.workflow_invocations (
                    invocation_key, tenant_id, user_id, conversation_id, request_id,
                    workflow_run_id, continuation_id, inbound_turn_key,
                    request_fingerprint, admission_status, pinned_versions,
                    version, created_at, updated_at, retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'START_QUEUED',%s,0,%s,%s,%s)
            """, (
                str(identity.invocation_key), str(identity.tenant_id),
                str(identity.user_id), str(identity.conversation_id),
                str(identity.request_id), str(identity.workflow_run_id),
                str(identity.continuation_id), str(identity.turn_key), fingerprint,
                Jsonb(dict(command.pinned_versions)), command.created_at,
                command.created_at, command.retention_until,
            ))
            self.fault_hook("after_invocation")

            outbox_id = identity.operation_key(
                "Application", "WorkflowStartRequested", str(identity.workflow_run_id),
            )
            connection.execute("""
                INSERT INTO dialogpilot_app.workflow_start_outbox (
                    outbox_id, invocation_key, workflow_run_id, payload,
                    available_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                str(outbox_id), str(identity.invocation_key),
                str(identity.workflow_run_id), Jsonb({
                    "pinned_versions": dict(command.pinned_versions),
                    "tenant_id": str(identity.tenant_id),
                    "user_id": str(identity.user_id),
                    "runtime_kind": command.runtime_kind,
                }), command.created_at, command.created_at,
            ))
            self.fault_hook("after_start_outbox")
            return AdmissionCreated(record)

    def _assert_capacity(self, connection):
        # Start and execution rows refer to the same invocation. Count once,
        # including claimed/running work until its durable acknowledgement.
        pending = connection.execute("""
            SELECT count(*) FROM (
                SELECT invocation_key FROM dialogpilot_app.workflow_start_outbox
                WHERE acknowledged_at IS NULL AND payload->>'runtime_kind'='target'
                UNION
                SELECT job.invocation_key FROM dialogpilot_app.compatibility_execution_outbox job
                JOIN dialogpilot_app.workflow_invocations invocation
                  ON invocation.invocation_key=job.invocation_key
                WHERE job.acknowledged_at IS NULL AND invocation.execution_runtime_kind='target'
            ) outstanding
        """).fetchone()[0]
        if pending >= self.max_pending:
            decisions.labels("target_queue", "rejected").inc()
            raise AdmissionCapacityExceeded("TARGET_QUEUE_FULL")

    def admit_resume(
        self, command: ValidResumeInbound | InvalidResumeInbound,
    ) -> ResumeAdmissionResult:
        identity = command.identity
        if isinstance(command, ValidResumeInbound):
            if any((
                command.binding.signal_id != command.target.signal_id,
                command.binding.expected_version != command.target.expected_version,
                command.binding.kind != command.target.kind,
                command.binding.schema_fingerprint != command.target.schema_fingerprint,
            )):
                command = InvalidResumeInbound(
                    identity, command.message, command.binding,
                    ResumeRejectionCode.VERSION_MISMATCH, command.created_at,
                    command.retention_until,
                )
        with self.pool.transaction() as connection:
            self._lock_conversation(connection, identity)
            turn_result = self._append_resume_turn(connection, command)
            if turn_result == "conflict":
                return ResumeRejected(
                    ResumeRejectionCode.IDEMPOTENCY_CONFLICT,
                    _fact_id("event", str(identity.turn_key)),
                )
            if isinstance(command, InvalidResumeInbound):
                event_id = self._append_resume_event(
                    connection, command, "RESUME_REJECTED", {
                        "signal_id": command.binding.signal_id,
                        "reason_code": command.code.value,
                        "inbound_turn_key": str(identity.turn_key),
                    },
                )
                return ResumeRejected(command.code, event_id)

            outbox_id = identity.operation_key(
                "Application", "ResumeRequested", command.target.signal_id,
            )
            existing = connection.execute("""
                SELECT inbound_turn_key, workflow_run_id FROM
                    dialogpilot_app.resume_requested_outbox
                WHERE signal_id=%s
            """, (command.target.signal_id,)).fetchone()
            if existing is not None:
                if existing == (
                    str(identity.turn_key), command.target.workflow_run_id,
                ):
                    return ResumeQueued(
                        outbox_id, command.target.workflow_run_id,
                        command.target.signal_id,
                    )
                return ResumeRejected(
                    ResumeRejectionCode.IDEMPOTENCY_CONFLICT,
                    _fact_id("event", command.target.signal_id),
                )
            self._append_resume_event(
                connection, command, "RESUME_REQUESTED", {
                    "signal_id": command.target.signal_id,
                    "expected_version": command.target.expected_version,
                    "workflow_run_id": command.target.workflow_run_id,
                    "inbound_turn_key": str(identity.turn_key),
                },
            )
            connection.execute("""
                INSERT INTO dialogpilot_app.resume_requested_outbox (
                    outbox_id, signal_id, expected_signal_version, workflow_run_id,
                    inbound_turn_key, tenant_id, user_id, conversation_id, payload,
                    available_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                str(outbox_id), command.target.signal_id,
                command.target.expected_version, command.target.workflow_run_id,
                str(identity.turn_key), str(identity.tenant_id), str(identity.user_id),
                str(identity.conversation_id), Jsonb({
                    "kind": command.target.kind,
                    "schema_fingerprint": command.target.schema_fingerprint,
                    "message": command.message,
                }), command.created_at, command.created_at,
            ))
            return ResumeQueued(
                outbox_id, command.target.workflow_run_id, command.target.signal_id,
            )

    def _append_resume_turn(self, connection, command) -> str:
        identity = command.identity
        existing = connection.execute("""
            SELECT content_sha256 FROM dialogpilot_app.conversation_turns
            WHERE turn_key=%s
        """, (str(identity.turn_key),)).fetchone()
        expected_hash = content_hash({
            "role": "inbound",
            "content": command.message,
            "resume_binding": {
                "signal_id": command.binding.signal_id,
                "expected_version": command.binding.expected_version,
                "kind": command.binding.kind,
                "reply_to_publication_id": command.binding.reply_to_publication_id,
                "schema_fingerprint": command.binding.schema_fingerprint,
            },
        })
        if existing is not None:
            return "existing" if existing[0] == expected_hash else "conflict"
        seq = self._next_seq(connection, identity, "next_turn_seq")
        connection.execute("""
            INSERT INTO dialogpilot_app.conversation_turns (
                turn_key, turn_id, tenant_id, user_id, conversation_id, seq,
                role, content, content_sha256, request_id, metadata, created_at,
                retention_until
            ) VALUES (%s,%s,%s,%s,%s,%s,'inbound',%s,%s,%s,%s,%s,%s)
        """, (
            str(identity.turn_key), str(identity.turn_id), str(identity.tenant_id),
            str(identity.user_id), str(identity.conversation_id), seq,
            command.message, expected_hash, str(identity.request_id),
            Jsonb({"resume_signal_id": command.binding.signal_id}),
            command.created_at, command.retention_until,
        ))
        self._advance_seq(connection, identity, "next_turn_seq", command.created_at)
        return "applied"

    def _append_resume_event(self, connection, command, event_type, payload) -> str:
        identity = command.identity
        operation = identity.operation_key(
            "Application", event_type, command.binding.signal_id,
        )
        event_id = _fact_id("event", str(operation))
        existing = connection.execute(
            "SELECT content_sha256 FROM dialogpilot_app.conversation_events "
            "WHERE operation_key=%s", (str(operation),),
        ).fetchone()
        expected_hash = content_hash({"event_type": event_type, "payload": payload})
        if existing is not None:
            if existing[0] != expected_hash:
                raise ValueError("resume event idempotency conflict")
            return event_id
        seq = self._next_seq(connection, identity, "next_event_seq")
        connection.execute("""
            INSERT INTO dialogpilot_app.conversation_events (
                event_id, operation_key, tenant_id, user_id, conversation_id,
                seq, event_type, payload, content_sha256, request_id,
                created_at, retention_until
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            event_id, str(operation), str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id), seq, event_type, Jsonb(payload),
            expected_hash, str(identity.request_id), command.created_at,
            command.retention_until,
        ))
        self._advance_seq(connection, identity, "next_event_seq", command.created_at)
        return event_id

    @staticmethod
    def _lock_conversation(connection, identity) -> None:
        connection.execute("""
            INSERT INTO dialogpilot_app.conversations (
                tenant_id, user_id, conversation_id
            ) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
        """, (
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        ))
        connection.execute("""
            SELECT 1 FROM dialogpilot_app.conversations
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s FOR UPDATE
        """, (
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        )).fetchone()

    @staticmethod
    def _next_seq(connection, identity, column: str) -> int:
        if column not in {"next_turn_seq", "next_event_seq"}:
            raise ValueError("unsupported sequence owner")
        return connection.execute(f"""
            SELECT {column} FROM dialogpilot_app.conversations
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        )).fetchone()[0]

    @staticmethod
    def _advance_seq(connection, identity, column: str, updated_at: str) -> None:
        if column not in {"next_turn_seq", "next_event_seq"}:
            raise ValueError("unsupported sequence owner")
        connection.execute(f"""
            UPDATE dialogpilot_app.conversations
            SET {column}={column}+1, updated_at=%s
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (
            updated_at, str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        ))

    @staticmethod
    def _invocation(connection, invocation_key: InvocationKey):
        with connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                "SELECT * FROM dialogpilot_app.workflow_invocations "
                "WHERE invocation_key=%s", (str(invocation_key),),
            ).fetchone()

    @staticmethod
    def _admission_replay(row, expected: AdmissionRecord) -> AdmissionResult:
        actual = PostgresInvocationRepository._record(row)
        if actual.request_fingerprint != expected.request_fingerprint:
            return AdmissionConflict(actual)
        return AdmissionExisting(actual)


class PostgresStartOutbox:
    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def claim(self, command: ClaimStart) -> tuple[StartOutboxItem, ...]:
        with self.pool.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute("""
                    WITH candidates AS (
                        SELECT outbox_id FROM dialogpilot_app.workflow_start_outbox
                        WHERE acknowledged_at IS NULL AND available_at <= %s
                          AND (claimed_by IS NULL OR lease_until <= %s)
                          AND COALESCE(payload->>'runtime_kind', 'compat')=%s
                        ORDER BY created_at, outbox_id
                        FOR UPDATE SKIP LOCKED LIMIT %s
                    )
                    UPDATE dialogpilot_app.workflow_start_outbox AS item
                    SET claimed_by=%s, lease_until=%s, attempts=attempts+1
                    FROM candidates WHERE item.outbox_id=candidates.outbox_id
                    RETURNING item.*
                """, (
                    command.now, command.now, command.runtime_kind, command.limit,
                    command.worker_id, command.lease_until,
                )).fetchall()
                result = []
                for row in rows:
                    invocation = cursor.execute("""
                        SELECT tenant_id, user_id, pinned_versions
                        FROM dialogpilot_app.workflow_invocations
                        WHERE invocation_key=%s
                    """, (row["invocation_key"],)).fetchone()
                    result.append(StartOutboxItem(
                        outbox_id=OperationKey(row["outbox_id"]),
                        invocation_key=InvocationKey(row["invocation_key"]),
                        workflow_run_id=WorkflowRunId(row["workflow_run_id"]),
                        tenant_id=invocation["tenant_id"],
                        user_id=invocation["user_id"],
                        pinned_versions=dict(invocation["pinned_versions"]),
                        attempt=int(row["attempts"]),
                        available_at=row["available_at"].isoformat(),
                        claimed_by=row["claimed_by"],
                        lease_until=row["lease_until"].isoformat(),
                    ))
                return tuple(result)

    def renew(
        self,
        outbox_id: OperationKey,
        *,
        worker_id: str,
        attempt: int,
        now: str,
        lease_until: str,
    ) -> bool:
        """Renew only the exact claim epoch held by this dispatcher.

        ``worker_id`` is operational identity, not a fencing token: a process may
        restart with the same name after another worker has reclaimed the row.
        The monotonically increasing attempt is the lease epoch and therefore
        participates in every ownership mutation.
        """
        with self.pool.transaction() as connection:
            result = connection.execute("""
                UPDATE dialogpilot_app.workflow_start_outbox
                SET lease_until=%s
                WHERE outbox_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL AND lease_until > %s
            """, (lease_until, str(outbox_id), worker_id, attempt, now))
            return result.rowcount == 1

    def acknowledge(
        self, outbox_id: OperationKey, *, worker_id: str, attempt: int,
    ) -> bool:
        with self.pool.transaction() as connection:
            result = connection.execute("""
                UPDATE dialogpilot_app.workflow_start_outbox
                SET acknowledged_at=transaction_timestamp()
                WHERE outbox_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
            """, (str(outbox_id), worker_id, attempt))
            if result.rowcount == 1:
                return True
            row = connection.execute(
                "SELECT acknowledged_at FROM dialogpilot_app.workflow_start_outbox "
                "WHERE outbox_id=%s", (str(outbox_id),),
            ).fetchone()
            return bool(row and row[0])

    def release(
        self,
        outbox_id: OperationKey,
        *,
        worker_id: str,
        attempt: int,
        available_at: str,
    ) -> bool:
        with self.pool.transaction() as connection:
            result = connection.execute("""
                UPDATE dialogpilot_app.workflow_start_outbox
                SET claimed_by=NULL, lease_until=NULL, available_at=%s
                WHERE outbox_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
            """, (available_at, str(outbox_id), worker_id, attempt))
            return result.rowcount == 1

    def is_acknowledged(self, outbox_id: OperationKey) -> bool:
        with self.pool.transaction() as connection:
            row = connection.execute(
                "SELECT acknowledged_at FROM dialogpilot_app.workflow_start_outbox "
                "WHERE outbox_id=%s", (str(outbox_id),),
            ).fetchone()
            return bool(row and row[0])


class StartOutboxDispatcher:
    def __init__(
        self,
        outbox: PostgresStartOutbox,
        invocations: PostgresInvocationRepository,
        binder: StableRunBinder,
        *,
        fault_hook: FaultHook | None = None,
    ):
        self.outbox = outbox
        self.invocations = invocations
        self.binder = binder
        self.fault_hook = fault_hook or (lambda _stage: None)

    def dispatch_once(self, command: ClaimStart) -> tuple[DispatchAttempt, ...]:
        results = []
        for item in self.outbox.claim(command):
            try:
                pointer = self.binder.get_or_create(
                    invocation_key=item.invocation_key,
                    workflow_run_id=item.workflow_run_id,
                    pinned_versions=item.pinned_versions,
                )
                self.fault_hook("after_run_binding")
                cas = self.invocations.compare_and_set(
                    item.invocation_key,
                    expected_status=AdmissionStatus.START_QUEUED,
                    expected_version=0,
                    target_status=AdmissionStatus.EXECUTION_BOUND,
                    execution_pointer=pointer,
                    tenant_id=item.tenant_id,
                    user_id=item.user_id,
                )
                if not isinstance(cas, (CasApplied, CasAlreadyApplied)):
                    raise RuntimeError("admission binding CAS rejected")
                self.fault_hook("after_admission_cas")
                if not self.outbox.acknowledge(
                    item.outbox_id, worker_id=command.worker_id,
                    attempt=item.attempt,
                ):
                    raise RuntimeError("start outbox ACK rejected")
                self.fault_hook("after_outbox_ack")
                results.append(DispatchAttempt(
                    item.outbox_id, "bound", pointer.run_id,
                ))
            except Exception as exc:
                if self.outbox.is_acknowledged(item.outbox_id):
                    results.append(DispatchAttempt(
                        item.outbox_id, "bound", error_type=type(exc).__name__,
                    ))
                    continue
                self.outbox.release(
                    item.outbox_id,
                    worker_id=command.worker_id,
                    attempt=item.attempt,
                    available_at=command.lease_until,
                )
                results.append(DispatchAttempt(
                    item.outbox_id, "retry", error_type=type(exc).__name__,
                ))
        return tuple(results)


def _fact_id(namespace: str, stable_value: str) -> str:
    digest = hashlib.sha256(stable_value.encode("utf-8")).hexdigest()
    return f"{namespace}:v1:{digest}"
