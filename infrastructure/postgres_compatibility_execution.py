"""PostgreSQL claim-epoch owner for whole compatibility invocations."""
from __future__ import annotations

import hashlib

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.admission_contract import ExecutionPointer
from application.compatibility_execution import (
    CompatibilityExecutionClaimLost,
    CompatibilityExecutionItem,
    CompatibilityExecutionTerminal,
)
from core.identity import InvocationKey, WorkflowRunId


class PostgresCompatibilityRunBinder:
    """Create exactly one durable work item before binding the opaque run."""

    runtime_kind = "compat"
    runtime_version = "chat-application-compat-v1"

    def __init__(self, pool):
        self.pool = pool

    def get_or_create(
        self,
        *,
        invocation_key: InvocationKey,
        workflow_run_id: WorkflowRunId,
        pinned_versions,
    ) -> ExecutionPointer:
        del pinned_versions
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT invocation.workflow_run_id, invocation.tenant_id,
                       invocation.user_id, invocation.conversation_id,
                       invocation.created_at, conversation.deletion_epoch,
                       conversation.deleted_at
                FROM dialogpilot_app.workflow_invocations invocation
                JOIN dialogpilot_app.conversations conversation
                  ON conversation.tenant_id=invocation.tenant_id
                 AND conversation.user_id=invocation.user_id
                 AND conversation.conversation_id=invocation.conversation_id
                WHERE invocation.invocation_key=%s FOR UPDATE OF invocation
            """, (str(invocation_key),)).fetchone()
            if row is None or str(row[0]) != str(workflow_run_id):
                raise RuntimeError("compatibility run binding identity changed")
            if row[6] is not None:
                raise RuntimeError("deleted conversation cannot bind execution")
            job_id = _job_id(str(invocation_key))
            connection.execute("""
                INSERT INTO dialogpilot_app.compatibility_execution_outbox (
                    job_id, invocation_key, workflow_run_id, tenant_id, user_id,
                    conversation_id, source_deletion_epoch, runtime_version,
                    available_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (invocation_key) DO NOTHING
            """, (
                job_id, str(invocation_key), str(workflow_run_id), row[1], row[2],
                row[3], int(row[5]), self.runtime_version, row[4], row[4],
            ))
            existing = connection.execute("""
                SELECT job_id, workflow_run_id, runtime_version
                FROM dialogpilot_app.compatibility_execution_outbox
                WHERE invocation_key=%s
            """, (str(invocation_key),)).fetchone()
            if existing != (job_id, str(workflow_run_id), self.runtime_version):
                raise RuntimeError("compatibility work item identity changed")
        return ExecutionPointer(
            self.runtime_kind, self.runtime_version, str(workflow_run_id),
        )


class PostgresCompatibilityExecutionOutbox:
    def __init__(self, pool):
        self.pool = pool

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 1,
        invocation_key: InvocationKey | None = None,
    ) -> tuple[CompatibilityExecutionItem, ...]:
        if not worker_id.strip() or lease_seconds < 1 or limit < 1 or limit > 100:
            raise ValueError("invalid compatibility execution claim")
        key_filter = " AND job.invocation_key=%s" if invocation_key else ""
        parameters = (
            [str(invocation_key), limit, worker_id, lease_seconds]
            if invocation_key else [limit, worker_id, lease_seconds]
        )
        with self.pool.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute("""
                    WITH candidates AS (
                        SELECT job.job_id
                        FROM dialogpilot_app.compatibility_execution_outbox job
                        JOIN dialogpilot_app.workflow_invocations invocation
                          ON invocation.invocation_key=job.invocation_key
                        JOIN dialogpilot_app.conversations conversation
                          ON conversation.tenant_id=job.tenant_id
                         AND conversation.user_id=job.user_id
                         AND conversation.conversation_id=job.conversation_id
                        WHERE job.acknowledged_at IS NULL
                          AND job.available_at <= transaction_timestamp()
                          AND (job.lease_until IS NULL OR
                               job.lease_until <= transaction_timestamp())
                          AND invocation.admission_status='EXECUTION_BOUND'
                          AND invocation.execution_runtime_kind='compat'
                          AND invocation.execution_run_id=job.workflow_run_id
                          AND conversation.deleted_at IS NULL
                          AND conversation.deletion_epoch=job.source_deletion_epoch
                    """ + key_filter + """
                        ORDER BY job.available_at, job.created_at, job.job_id
                        FOR UPDATE OF job SKIP LOCKED LIMIT %s
                    )
                    UPDATE dialogpilot_app.compatibility_execution_outbox job
                    SET claimed_by=%s,
                        lease_until=transaction_timestamp() + (%s * interval '1 second'),
                        attempts=attempts+1, last_error_code=NULL
                    FROM candidates,
                         dialogpilot_app.workflow_invocations invocation,
                         dialogpilot_app.conversation_turns turn
                    WHERE job.job_id=candidates.job_id
                      AND invocation.invocation_key=job.invocation_key
                      AND turn.turn_key=invocation.inbound_turn_key
                    RETURNING job.*, invocation.request_id,
                              invocation.continuation_id,
                              invocation.pinned_versions, turn.content
                """, tuple(parameters)).fetchall()
        return tuple(self._item(row) for row in rows)

    def renew(
        self, item: CompatibilityExecutionItem, *, worker_id: str,
        lease_seconds: int,
    ) -> None:
        with self.pool.transaction() as connection:
            updated = connection.execute("""
                UPDATE dialogpilot_app.compatibility_execution_outbox
                SET lease_until=transaction_timestamp() + (%s * interval '1 second')
                WHERE job_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
                  AND lease_until > transaction_timestamp()
            """, (lease_seconds, item.job_id, worker_id, item.attempt)).rowcount
        if updated != 1:
            raise CompatibilityExecutionClaimLost("compatibility execution lease is not owned")

    def assert_owned(
        self, item: CompatibilityExecutionItem, *, worker_id: str,
    ) -> None:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT 1 FROM dialogpilot_app.compatibility_execution_outbox job
                JOIN dialogpilot_app.conversations conversation
                  ON conversation.tenant_id=job.tenant_id
                 AND conversation.user_id=job.user_id
                 AND conversation.conversation_id=job.conversation_id
                WHERE job.job_id=%s AND job.claimed_by=%s AND job.attempts=%s
                  AND job.acknowledged_at IS NULL
                  AND job.lease_until > transaction_timestamp()
                  AND conversation.deleted_at IS NULL
                  AND conversation.deletion_epoch=job.source_deletion_epoch
            """, (item.job_id, worker_id, item.attempt)).fetchone()
        if row is None:
            raise CompatibilityExecutionClaimLost("compatibility execution lease is not owned")

    def acknowledge(
        self,
        item: CompatibilityExecutionItem,
        *,
        worker_id: str,
        terminal: CompatibilityExecutionTerminal,
    ) -> None:
        terminal_payload = {
            "outcome_type": terminal.outcome_type,
            "payload": dict(terminal.payload),
            "terminal_ref": terminal.terminal_ref,
        }
        with self.pool.transaction() as connection:
            updated = connection.execute("""
                UPDATE dialogpilot_app.compatibility_execution_outbox
                SET acknowledged_at=transaction_timestamp(), outcome_type=%s,
                    terminal_ref=%s, claimed_by=NULL, lease_until=NULL
                WHERE job_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
                  AND lease_until > transaction_timestamp()
            """, (
                terminal.outcome_type, Jsonb(terminal_payload), item.job_id,
                worker_id, item.attempt,
            )).rowcount
            if updated != 1:
                raise CompatibilityExecutionClaimLost(
                    "compatibility execution lease is not owned"
                )
            invocation = connection.execute("""
                UPDATE dialogpilot_app.workflow_invocations
                SET terminal_ref=%s, updated_at=transaction_timestamp()
                WHERE invocation_key=%s
                  AND (terminal_ref IS NULL OR terminal_ref=%s)
            """, (
                Jsonb(terminal_payload), str(item.invocation_key),
                Jsonb(terminal_payload),
            ))
            if invocation.rowcount != 1:
                raise CompatibilityExecutionClaimLost(
                    "invocation terminal fact conflicts"
                )

    def release(
        self,
        item: CompatibilityExecutionItem,
        *,
        worker_id: str,
        error_code: str,
        retry_after_seconds: int = 0,
    ) -> None:
        with self.pool.transaction() as connection:
            updated = connection.execute("""
                UPDATE dialogpilot_app.compatibility_execution_outbox
                SET claimed_by=NULL, lease_until=NULL,
                    available_at=transaction_timestamp() + (%s * interval '1 second'),
                    last_error_code=%s
                WHERE job_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
            """, (
                max(0, retry_after_seconds), error_code[:200], item.job_id,
                worker_id, item.attempt,
            )).rowcount
        if updated != 1:
            raise CompatibilityExecutionClaimLost("compatibility execution lease is not owned")

    def terminal(
        self, invocation_key: InvocationKey,
    ) -> CompatibilityExecutionTerminal | None:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT terminal_ref FROM dialogpilot_app.workflow_invocations
                WHERE invocation_key=%s
            """, (str(invocation_key),)).fetchone()
        if row is None or row[0] is None:
            return None
        value = dict(row[0])
        return CompatibilityExecutionTerminal(
            str(value["outcome_type"]), dict(value["payload"]),
            str(value["terminal_ref"]),
        )

    @staticmethod
    def _item(row) -> CompatibilityExecutionItem:
        return CompatibilityExecutionItem(
            job_id=row["job_id"],
            invocation_key=InvocationKey(row["invocation_key"]),
            workflow_run_id=WorkflowRunId(row["workflow_run_id"]),
            tenant_id=row["tenant_id"], user_id=row["user_id"],
            conversation_id=row["conversation_id"], request_id=row["request_id"],
            continuation_id=row["continuation_id"], message=row["content"],
            pinned_versions={
                str(key): str(value)
                for key, value in dict(row["pinned_versions"]).items()
            },
            deletion_epoch=int(row["source_deletion_epoch"]),
            attempt=int(row["attempts"]), claimed_by=row["claimed_by"],
            lease_until=row["lease_until"].isoformat(),
        )


def _job_id(invocation_key: str) -> str:
    digest = hashlib.sha256(invocation_key.encode("utf-8")).hexdigest()
    return f"compat-execution:v1:{digest}"
