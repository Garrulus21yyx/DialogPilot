"""PostgreSQL single-owner repositories for immutable conversation facts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.admission_contract import (
    AdmissionConflict,
    AdmissionCreated,
    AdmissionExisting,
    AdmissionRecord,
    AdmissionResult,
    AdmissionStatus,
    CasAlreadyApplied,
    CasApplied,
    CasRejected,
    ExecutionPointer,
    validate_admission_transition,
)
from application.conversation_store import (
    AppendStatus,
    AppendTurnResult,
    ConversationAccessDenied,
    ConversationNotFound,
    ConversationScope,
    ConversationTurn,
    EventToAppend,
    TurnRole,
    TurnToAppend,
)
from core.identity import (
    ContinuationId,
    ConversationId,
    InvocationKey,
    RequestId,
    TenantId,
    TurnId,
    TurnKey,
    UserId,
    WorkflowRunId,
)
from infrastructure.postgres import PostgresPool


@dataclass(frozen=True)
class InvocationToCreate:
    record: AdmissionRecord
    scope: ConversationScope
    request_id: RequestId
    continuation_id: ContinuationId
    inbound_turn_key: TurnKey
    created_at: str
    retention_until: str | None = None


class PostgresConversationTurnStore:
    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def append_turn(
        self, scope: ConversationScope, turn: TurnToAppend,
    ) -> AppendTurnResult:
        with self.pool.transaction() as connection:
            existing = self._turn_by_key(connection, str(turn.turn_key))
            if existing is not None:
                self._assert_row_scope(existing, scope)
                return self._turn_replay(existing, turn)
            self._lock_scope(connection, scope, create=True)
            # A concurrent writer may have inserted the same global key while
            # this transaction waited for the conversation row lock.
            existing = self._turn_by_key(connection, str(turn.turn_key))
            if existing is not None:
                self._assert_row_scope(existing, scope)
                return self._turn_replay(existing, turn)
            seq = connection.execute("""
                SELECT next_turn_seq FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, self._scope_params(scope)).fetchone()[0]
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_turns (
                    turn_key, turn_id, tenant_id, user_id, conversation_id,
                    seq, role, content, content_sha256, request_id,
                    invocation_key, metadata, created_at, retention_until
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                str(turn.turn_key), str(turn.turn_id),
                str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id),
                seq, turn.role.value, turn.content, turn.content_sha256,
                str(turn.request_id) if turn.request_id else None,
                str(turn.invocation_key) if turn.invocation_key else None,
                Jsonb(dict(turn.metadata or {})), turn.created_at,
                turn.retention_until,
            ))
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET next_turn_seq=next_turn_seq+1, updated_at=%s
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (turn.created_at, *self._scope_params(scope)))
            row = self._turn_by_key(connection, str(turn.turn_key))
            return AppendTurnResult(AppendStatus.APPLIED, self._turn(row))

    def list_turns(
        self, scope: ConversationScope, *, after_seq: int = 0, limit: int = 100,
    ) -> tuple[ConversationTurn, ...]:
        if after_seq < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid turn page")
        with self.pool.transaction() as connection:
            self._lock_scope(connection, scope, create=False, for_update=False)
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute("""
                    SELECT * FROM dialogpilot_app.conversation_turns
                    WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                      AND seq>%s
                    ORDER BY seq ASC LIMIT %s
                """, (*self._scope_params(scope), after_seq, limit)).fetchall()
        return tuple(self._turn(row) for row in rows)

    def append_event(
        self, scope: ConversationScope, event: EventToAppend,
    ) -> AppendStatus:
        with self.pool.transaction() as connection:
            existing = self._event_by_operation(connection, str(event.operation_key))
            if existing is not None:
                self._assert_row_scope(existing, scope)
                return (
                    AppendStatus.ALREADY_APPLIED
                    if self._same_event(existing, event)
                    else AppendStatus.IDEMPOTENCY_CONFLICT
                )
            self._lock_scope(connection, scope, create=True)
            existing = self._event_by_operation(connection, str(event.operation_key))
            if existing is not None:
                self._assert_row_scope(existing, scope)
                return (
                    AppendStatus.ALREADY_APPLIED
                    if self._same_event(existing, event)
                    else AppendStatus.IDEMPOTENCY_CONFLICT
                )
            seq = connection.execute("""
                SELECT next_event_seq FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, self._scope_params(scope)).fetchone()[0]
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_events (
                    event_id, operation_key, tenant_id, user_id, conversation_id,
                    seq, event_type, payload, content_sha256, request_id,
                    invocation_key, created_at, retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                event.event_id, str(event.operation_key), str(scope.tenant_id),
                str(scope.user_id), str(scope.conversation_id), seq,
                event.event_type, Jsonb(dict(event.payload)), event.content_sha256,
                str(event.request_id) if event.request_id else None,
                str(event.invocation_key) if event.invocation_key else None,
                event.created_at, event.retention_until,
            ))
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET next_event_seq=next_event_seq+1, updated_at=%s
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (event.created_at, *self._scope_params(scope)))
            return AppendStatus.APPLIED

    def _lock_scope(
        self,
        connection,
        scope: ConversationScope,
        *,
        create: bool,
        for_update: bool = True,
    ) -> None:
        if create:
            connection.execute("""
                INSERT INTO dialogpilot_app.conversations (
                    tenant_id, user_id, conversation_id
                ) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
            """, self._scope_params(scope))
        suffix = " FOR UPDATE" if for_update else ""
        row = connection.execute("""
            SELECT 1 FROM dialogpilot_app.conversations
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """ + suffix, self._scope_params(scope)).fetchone()
        if row is not None:
            return
        any_scope = connection.execute("""
            SELECT 1 FROM dialogpilot_app.conversations
            WHERE conversation_id=%s LIMIT 1
        """, (str(scope.conversation_id),)).fetchone()
        if any_scope:
            raise ConversationAccessDenied("conversation scope does not match")
        raise ConversationNotFound(str(scope.conversation_id))

    @staticmethod
    def _scope_params(scope: ConversationScope) -> tuple[str, str, str]:
        return str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id)

    @staticmethod
    def _turn_by_key(connection, turn_key: str):
        with connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                "SELECT * FROM dialogpilot_app.conversation_turns WHERE turn_key=%s",
                (turn_key,),
            ).fetchone()

    @staticmethod
    def _event_by_operation(connection, operation_key: str):
        with connection.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(
                "SELECT * FROM dialogpilot_app.conversation_events "
                "WHERE operation_key=%s", (operation_key,),
            ).fetchone()

    @staticmethod
    def _assert_row_scope(row, scope: ConversationScope) -> None:
        expected = (str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id))
        actual = (row["tenant_id"], row["user_id"], row["conversation_id"])
        if actual != expected:
            raise ConversationAccessDenied("fact belongs to another scope")

    def _turn_replay(self, row, turn: TurnToAppend) -> AppendTurnResult:
        same = all((
            row["turn_id"] == str(turn.turn_id),
            row["role"] == turn.role.value,
            row["content_sha256"] == turn.content_sha256,
            row["request_id"] == (str(turn.request_id) if turn.request_id else None),
            row["invocation_key"] == (
                str(turn.invocation_key) if turn.invocation_key else None
            ),
        ))
        return AppendTurnResult(
            AppendStatus.ALREADY_APPLIED if same else AppendStatus.IDEMPOTENCY_CONFLICT,
            self._turn(row),
        )

    @staticmethod
    def _same_event(row, event: EventToAppend) -> bool:
        return all((
            row["event_id"] == event.event_id,
            row["event_type"] == event.event_type,
            row["content_sha256"] == event.content_sha256,
            row["request_id"] == (str(event.request_id) if event.request_id else None),
            row["invocation_key"] == (
                str(event.invocation_key) if event.invocation_key else None
            ),
        ))

    @staticmethod
    def _turn(row) -> ConversationTurn:
        scope = ConversationScope(
            TenantId(row["tenant_id"]), UserId(row["user_id"]),
            ConversationId(row["conversation_id"]),
        )
        return ConversationTurn(
            turn_key=TurnKey(row["turn_key"]), turn_id=TurnId(row["turn_id"]),
            scope=scope, seq=int(row["seq"]), role=TurnRole(row["role"]),
            content=row["content"], content_sha256=row["content_sha256"],
            request_id=RequestId(row["request_id"]) if row["request_id"] else None,
            invocation_key=(
                InvocationKey(row["invocation_key"]) if row["invocation_key"] else None
            ),
            metadata=dict(row["metadata"]), created_at=row["created_at"].isoformat(),
        )


class PostgresInvocationRepository:
    def __init__(
        self, pool: PostgresPool,
        *,
        now: Callable[[], datetime] | None = None,
    ):
        self.pool = pool
        self.now = now or (lambda: datetime.now(timezone.utc))

    def put_queued(self, command: InvocationToCreate) -> AdmissionResult:
        if command.record.status is not AdmissionStatus.START_QUEUED:
            raise ValueError("new invocation must be START_QUEUED")
        with self.pool.transaction() as connection:
            inserted = connection.execute("""
                INSERT INTO dialogpilot_app.workflow_invocations (
                    invocation_key, tenant_id, user_id, conversation_id, request_id,
                    workflow_run_id, continuation_id, inbound_turn_key,
                    request_fingerprint, admission_status, pinned_versions,
                    version, created_at, updated_at, retention_until
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (invocation_key) DO NOTHING
            """, (
                str(command.record.invocation_key), str(command.scope.tenant_id),
                str(command.scope.user_id), str(command.scope.conversation_id),
                str(command.request_id), str(command.record.workflow_run_id),
                str(command.continuation_id), str(command.inbound_turn_key),
                command.record.request_fingerprint, command.record.status.value,
                Jsonb(dict(command.record.pinned_versions)), command.record.version,
                command.created_at, command.created_at, command.retention_until,
            ))
            existing = self._get_row(
                connection, command.record.invocation_key,
                tenant_id=str(command.scope.tenant_id),
                user_id=str(command.scope.user_id),
            )
            if existing is None:
                raise RuntimeError("invocation insert was not observable")
            record = self._record(existing)
            same = all((
                record == command.record,
                existing["request_id"] == str(command.request_id),
                existing["continuation_id"] == str(command.continuation_id),
                existing["inbound_turn_key"] == str(command.inbound_turn_key),
            ))
            if not same:
                return AdmissionConflict(record)
            return AdmissionCreated(record) if inserted.rowcount == 1 else AdmissionExisting(record)

    def get(
        self, invocation_key: InvocationKey, *, tenant_id: str, user_id: str,
    ) -> AdmissionRecord | None:
        with self.pool.transaction() as connection:
            row = self._get_row(
                connection, invocation_key, tenant_id=tenant_id, user_id=user_id,
            )
            return self._record(row) if row else None

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
    ):
        validate_admission_transition(expected_status, target_status)
        if target_status is AdmissionStatus.EXECUTION_BOUND and not execution_pointer:
            raise ValueError("binding CAS requires execution pointer")
        if target_status is not AdmissionStatus.EXECUTION_BOUND and execution_pointer:
            raise ValueError("expiration CAS cannot carry execution pointer")
        with self.pool.transaction() as connection:
            row = self._get_row(
                connection, invocation_key, tenant_id=tenant_id, user_id=user_id,
                for_update=True,
            )
            if row is None:
                raise ConversationNotFound(str(invocation_key))
            current = self._record(row)
            if (
                current.status is target_status
                and current.execution_pointer == execution_pointer
            ):
                return CasAlreadyApplied(current)
            if current.status is not expected_status or current.version != expected_version:
                return CasRejected(current)
            now = self.now()
            connection.execute("""
                UPDATE dialogpilot_app.workflow_invocations
                SET admission_status=%s, execution_runtime_kind=%s,
                    execution_runtime_version=%s, execution_run_id=%s,
                    version=version+1, updated_at=%s
                WHERE invocation_key=%s AND tenant_id=%s AND user_id=%s
                  AND admission_status=%s AND version=%s
            """, (
                target_status.value,
                execution_pointer.runtime_kind if execution_pointer else None,
                execution_pointer.runtime_version if execution_pointer else None,
                execution_pointer.run_id if execution_pointer else None,
                now, str(invocation_key), tenant_id, user_id,
                expected_status.value, expected_version,
            ))
            updated = self._get_row(
                connection, invocation_key, tenant_id=tenant_id, user_id=user_id,
            )
            return CasApplied(self._record(updated))

    @staticmethod
    def _get_row(
        connection, invocation_key: InvocationKey, *, tenant_id: str, user_id: str,
        for_update: bool = False,
    ):
        suffix = " FOR UPDATE" if for_update else ""
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute("""
                SELECT * FROM dialogpilot_app.workflow_invocations
                WHERE invocation_key=%s AND tenant_id=%s AND user_id=%s
            """ + suffix, (str(invocation_key), tenant_id, user_id)).fetchone()
            if row is not None:
                return row
            any_scope = cursor.execute("""
                SELECT 1 FROM dialogpilot_app.workflow_invocations
                WHERE invocation_key=%s LIMIT 1
            """, (str(invocation_key),)).fetchone()
            if any_scope:
                raise ConversationAccessDenied("invocation belongs to another scope")
            return None

    @staticmethod
    def _record(row) -> AdmissionRecord:
        pointer = None
        if row["execution_run_id"]:
            pointer = ExecutionPointer(
                row["execution_runtime_kind"], row["execution_runtime_version"],
                row["execution_run_id"],
            )
        return AdmissionRecord(
            invocation_key=InvocationKey(row["invocation_key"]),
            workflow_run_id=WorkflowRunId(row["workflow_run_id"]),
            request_fingerprint=row["request_fingerprint"],
            status=AdmissionStatus(row["admission_status"]),
            version=int(row["version"]),
            pinned_versions=dict(row["pinned_versions"]),
            execution_pointer=pointer,
        )
