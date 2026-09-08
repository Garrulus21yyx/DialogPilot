"""PostgreSQL adapter from the shared invocation ledger to Target runs."""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.admission_contract import ExecutionPointer
from application.chat_contracts import Failed
from application.target_run import (
    TargetRunClaimLost,
    TargetRunItem,
    TargetRunTerminal,
    failure_payload,
    outcome_from_terminal,
)
from core.identity import InvocationKey, WorkflowRunId


_LEDGER = "dialogpilot_app.compatibility_execution_outbox"


class PostgresTargetRunBinder:
    """Bind one admitted Target invocation to one durable execution row."""

    runtime_kind = "target"
    runtime_version = "target-turn-runtime-v2"

    def __init__(self, pool) -> None:
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
                raise RuntimeError("target run binding identity changed")
            if row[6] is not None:
                raise RuntimeError("deleted conversation cannot bind target run")
            job_id = f"target:{invocation_key}"
            connection.execute(f"""
                INSERT INTO {_LEDGER} (
                    job_id, invocation_key, workflow_run_id, tenant_id, user_id,
                    conversation_id, source_deletion_epoch, runtime_version,
                    available_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (invocation_key) DO NOTHING
            """, (
                job_id, str(invocation_key), str(workflow_run_id), row[1], row[2],
                row[3], int(row[5]), self.runtime_version, row[4], row[4],
            ))
            existing = connection.execute(f"""
                SELECT job_id, workflow_run_id, runtime_version
                FROM {_LEDGER} WHERE invocation_key=%s
            """, (str(invocation_key),)).fetchone()
            if existing != (job_id, str(workflow_run_id), self.runtime_version):
                raise RuntimeError("target run identity changed")
        return ExecutionPointer(
            self.runtime_kind, self.runtime_version, str(workflow_run_id),
        )


class PostgresTargetRunStore:
    """Lease-fenced Target view over the shared durable invocation ledger."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 1,
        invocation_key: InvocationKey | None = None,
    ) -> tuple[TargetRunItem, ...]:
        if not worker_id.strip() or lease_seconds < 1 or not 1 <= limit <= 100:
            raise ValueError("invalid target run claim")
        key_filter = " AND job.invocation_key=%s" if invocation_key else ""
        parameters = (
            [str(invocation_key), limit, worker_id, lease_seconds]
            if invocation_key else [limit, worker_id, lease_seconds]
        )
        with self.pool.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(f"""
                    WITH candidates AS (
                        SELECT job.job_id
                        FROM {_LEDGER} job
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
                          AND invocation.execution_runtime_kind='target'
                          AND invocation.execution_run_id=job.workflow_run_id
                          AND conversation.deleted_at IS NULL
                          AND conversation.deletion_epoch=job.source_deletion_epoch
                """ + key_filter + f"""
                        ORDER BY job.available_at, job.created_at, job.job_id
                        FOR UPDATE OF job SKIP LOCKED LIMIT %s
                    )
                    UPDATE {_LEDGER} job
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
                              invocation.pinned_versions, turn.content,
                              turn.metadata AS turn_metadata
                """, tuple(parameters)).fetchall()
        return tuple(self._item(row) for row in rows)

    def renew(
        self, item: TargetRunItem, *, worker_id: str, lease_seconds: int,
    ) -> None:
        with self.pool.transaction() as connection:
            updated = connection.execute(f"""
                UPDATE {_LEDGER}
                SET lease_until=transaction_timestamp() + (%s * interval '1 second')
                WHERE workflow_run_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
                  AND lease_until > transaction_timestamp()
            """, (
                lease_seconds, str(item.run_id), worker_id, item.attempt,
            )).rowcount
        if updated != 1:
            raise TargetRunClaimLost("target run lease is not owned")

    def assert_owned(self, item: TargetRunItem, *, worker_id: str) -> None:
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                SELECT 1 FROM {_LEDGER} job
                JOIN dialogpilot_app.conversations conversation
                  ON conversation.tenant_id=job.tenant_id
                 AND conversation.user_id=job.user_id
                 AND conversation.conversation_id=job.conversation_id
                WHERE job.workflow_run_id=%s AND job.claimed_by=%s
                  AND job.attempts=%s AND job.acknowledged_at IS NULL
                  AND job.lease_until > transaction_timestamp()
                  AND conversation.deleted_at IS NULL
                  AND conversation.deletion_epoch=job.source_deletion_epoch
            """, (str(item.run_id), worker_id, item.attempt)).fetchone()
        if row is None:
            raise TargetRunClaimLost("target run lease is not owned")

    def select_failure(self, item: TargetRunItem, *, worker_id: str, failure: Failed) -> None:
        if failure.retryable:
            raise ValueError("only a terminal failure can be selected")
        payload = failure_payload(failure)
        with self.pool.transaction() as connection:
            updated = connection.execute(f"""
                UPDATE {_LEDGER}
                SET selected_failure=%s, last_error_code=%s,
                    attempt_failures=attempt_failures || %s::jsonb
                WHERE workflow_run_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL AND lease_until > transaction_timestamp()
                  AND (selected_failure IS NULL OR selected_failure=%s)
            """, (Jsonb(payload), failure.code[:200], Jsonb({str(item.attempt): payload}),
                str(item.run_id), worker_id, item.attempt, Jsonb(payload))).rowcount
        if updated != 1:
            raise TargetRunClaimLost("target run failure selection conflicts")

    def complete(
        self,
        item: TargetRunItem,
        *,
        worker_id: str,
        terminal: TargetRunTerminal,
    ) -> None:
        payload = {
            "status": terminal.status,
            "payload": dict(terminal.payload),
            "terminal_ref": terminal.terminal_ref,
        }
        with self.pool.transaction() as connection:
            updated = connection.execute(f"""
                UPDATE {_LEDGER}
                SET acknowledged_at=transaction_timestamp(), outcome_type=%s,
                    terminal_ref=%s, claimed_by=NULL, lease_until=NULL,
                    attempt_failures=attempt_failures || %s::jsonb
                WHERE workflow_run_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
                  AND lease_until > transaction_timestamp()
            """, (
                terminal.status, Jsonb(payload),
                Jsonb({str(item.attempt): dict(terminal.payload)} if terminal.status == "FAILED" else {}),
                str(item.run_id),
                worker_id, item.attempt,
            )).rowcount
            if updated != 1:
                raise TargetRunClaimLost("target run lease is not owned")
            invocation = connection.execute("""
                UPDATE dialogpilot_app.workflow_invocations
                SET terminal_ref=%s, updated_at=transaction_timestamp()
                WHERE invocation_key=%s
                  AND (terminal_ref IS NULL OR terminal_ref=%s)
            """, (
                Jsonb(payload), str(item.invocation_key), Jsonb(payload),
            ))
            if invocation.rowcount != 1:
                raise TargetRunClaimLost("invocation terminal fact conflicts")

    def release(
        self,
        item: TargetRunItem,
        *,
        worker_id: str,
        failure: Failed,
        retry_after_seconds: int = 0,
    ) -> None:
        with self.pool.transaction() as connection:
            updated = connection.execute(f"""
                UPDATE {_LEDGER}
                SET claimed_by=NULL, lease_until=NULL,
                    available_at=transaction_timestamp() + (%s * interval '1 second'),
                    last_error_code=%s,
                    attempt_failures=attempt_failures || %s::jsonb
                WHERE workflow_run_id=%s AND claimed_by=%s AND attempts=%s
                  AND acknowledged_at IS NULL
                  AND lease_until > transaction_timestamp()
            """, (
                max(0, retry_after_seconds), failure.code[:200],
                Jsonb({str(item.attempt): failure_payload(failure)}),
                str(item.run_id), worker_id, item.attempt,
            )).rowcount
        if updated != 1:
            raise TargetRunClaimLost("target run lease is not owned")

    def terminal(
        self, invocation_key: InvocationKey,
    ) -> TargetRunTerminal | None:
        with self.pool.transaction() as connection:
            row = connection.execute(f"""
                SELECT terminal_ref FROM {_LEDGER} WHERE invocation_key=%s
            """, (str(invocation_key),)).fetchone()
        if row is None or row[0] is None:
            return None
        value = dict(row[0])
        return TargetRunTerminal(
            str(value["status"]), dict(value["payload"]),
            str(value["terminal_ref"]),
        )

    def runtime(self, invocation) -> dict[str, object] | None:
        run_id = str(invocation.get("execution_run_id") or "")
        if not run_id:
            return None
        with self.pool.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(f"""
                    SELECT workflow_run_id, claimed_by, lease_until, attempts,
                           outcome_type, last_error_code, acknowledged_at, attempt_failures
                    FROM {_LEDGER} WHERE workflow_run_id=%s
                """, (run_id,)).fetchone()
        if row is None:
            return None
        status = (
            row["outcome_type"]
            if row["acknowledged_at"] is not None
            else "RUNNING" if row["claimed_by"] is not None else "QUEUED"
        )
        return {
            "runtime_kind": "target",
            "run_id": row["workflow_run_id"],
            "execution_status": status,
            "attempt": int(row["attempts"]),
            "reason_code": row["last_error_code"],
            # This projection is exposed by /invocations; full exception chains
            # remain in the diagnostic record, not the customer response.
            "attempt_failures": {attempt: {
                "code": failure["code"], "retryable": failure["retryable"],
                "stages": [{"stage": stage["stage"], "status": stage["status"],
                    "code": stage["detail"].get("code")}
                    for stage in failure.get("stages", ())],
            } for attempt, failure in dict(row["attempt_failures"]).items()},
        }

    @staticmethod
    def _item(row) -> TargetRunItem:
        return TargetRunItem(
            run_id=WorkflowRunId(row["workflow_run_id"]),
            invocation_key=InvocationKey(row["invocation_key"]),
            tenant_id=row["tenant_id"], user_id=row["user_id"],
            conversation_id=row["conversation_id"], request_id=row["request_id"],
            continuation_id=row["continuation_id"], message=row["content"],
            asset_ids=tuple(
                str(value)
                for value in dict(row["turn_metadata"]).get("asset_ids", ())
            ),
            pinned_versions={
                str(key): str(value)
                for key, value in dict(row["pinned_versions"]).items()
            },
            deletion_epoch=int(row["source_deletion_epoch"]),
            attempt=int(row["attempts"]), claimed_by=row["claimed_by"],
            selected_failure=(outcome_from_terminal(TargetRunTerminal(
                "FAILED", dict(row["selected_failure"]), str(row["invocation_key"])))
                if row["selected_failure"] is not None else None),
        )
