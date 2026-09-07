"""PostgreSQL conversation projection outbox and deletion-fence owner."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Mapping

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.conversation_projection import (
    ConversationProjectionAdapter,
    ConversationProjectionPolicyV1,
    ConversationSubject,
    ProjectableConversationEvent,
    ProjectionApplyStatus,
    ProjectionDispatchResult,
    ProjectionName,
    ProjectionOutboxOutcome,
    SubjectFence,
)
from application.conversation_store import content_hash
from infrastructure.postgres import PostgresPool


class ConversationDeletionError(RuntimeError):
    pass


class ProjectionClaimError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversationDeletion:
    deletion_id: str
    subject: ConversationSubject
    prior_epoch: int
    target_epoch: int
    reason_code: str
    actor: str
    created_at: str


class PostgresConversationDeletionRepository:
    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def delete(
        self,
        subject: ConversationSubject,
        *,
        reason_code: str,
        actor: str,
        created_at: str,
    ) -> ConversationDeletion:
        if not reason_code.strip() or not actor.strip():
            raise ValueError("deletion reason and actor are required")
        scope = (subject.tenant_id, subject.user_id, subject.conversation_id)
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT next_event_seq, deletion_epoch, deleted_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, scope).fetchone()
            if row is None:
                raise ConversationDeletionError("conversation does not exist")
            if row[2] is not None:
                existing = connection.execute("""
                    SELECT deletion_id, prior_epoch, target_epoch, reason_code,
                           actor, created_at
                    FROM dialogpilot_app.conversation_deletion_events
                    WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                    ORDER BY target_epoch DESC LIMIT 1
                """, scope).fetchone()
                return ConversationDeletion(
                    existing[0], subject, int(existing[1]), int(existing[2]),
                    existing[3], existing[4], existing[5].isoformat(),
                )
            prior_epoch = int(row[1])
            target_epoch = prior_epoch + 1
            deletion_id = _stable_id(
                "conversation-deletion",
                "\0".join(map(str, (*scope, target_epoch))),
            )
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET deletion_epoch=%s, deleted_at=%s, updated_at=%s,
                    next_event_seq=next_event_seq+1
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (target_epoch, created_at, created_at, *scope))
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_deletion_events (
                    deletion_id, tenant_id, user_id, conversation_id,
                    prior_epoch, target_epoch, reason_code, actor, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                deletion_id, *scope, prior_epoch, target_epoch,
                reason_code, actor, created_at,
            ))
            payload = {
                "deletion_id": deletion_id,
                "prior_epoch": prior_epoch,
                "target_epoch": target_epoch,
                "reason_code": reason_code,
            }
            event_id = _stable_id("conversation-deletion-event", deletion_id)
            operation_key = _stable_id("conversation-deletion-operation", deletion_id)
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_events (
                    event_id, operation_key, tenant_id, user_id, conversation_id,
                    seq, event_type, payload, content_sha256, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,'CONVERSATION_DELETED',%s,%s,%s)
            """, (
                event_id, operation_key, *scope, int(row[0]), Jsonb(payload),
                content_hash({
                    "event_type": "CONVERSATION_DELETED", "payload": payload,
                }), created_at,
            ))
            return ConversationDeletion(
                deletion_id, subject, prior_epoch, target_epoch,
                reason_code, actor, created_at,
            )

    def fence(self, subject: ConversationSubject) -> SubjectFence:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT deletion_epoch, deleted_at IS NOT NULL
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (
                subject.tenant_id, subject.user_id, subject.conversation_id,
            )).fetchone()
        if row is None:
            raise ConversationDeletionError("conversation does not exist")
        return SubjectFence(int(row[0]), bool(row[1]))


class PostgresConversationProjectionOutbox:
    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def claim(
        self,
        *,
        projection_name: ProjectionName,
        worker_id: str,
        now: str,
        lease_until: str,
        limit: int = 20,
    ) -> tuple[ProjectableConversationEvent, ...]:
        if limit < 1 or limit > 200:
            raise ValueError("projection claim limit must be between 1 and 200")
        with self.pool.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute("""
                    WITH candidates AS (
                        SELECT candidate.outbox_id
                        FROM dialogpilot_app.conversation_projection_outbox candidate
                        WHERE candidate.projection_name=%s
                          AND candidate.acknowledged_at IS NULL
                          AND candidate.available_at <= %s
                          AND (candidate.lease_until IS NULL OR candidate.lease_until <= %s)
                          AND NOT EXISTS (
                              SELECT 1
                              FROM dialogpilot_app.conversation_projection_outbox prior
                              WHERE prior.projection_name=candidate.projection_name
                                AND prior.generation=candidate.generation
                                AND prior.tenant_id=candidate.tenant_id
                                AND prior.user_id=candidate.user_id
                                AND prior.conversation_id=candidate.conversation_id
                                AND prior.event_seq < candidate.event_seq
                                AND prior.acknowledged_at IS NULL
                          )
                        ORDER BY candidate.available_at, candidate.created_at,
                                 candidate.outbox_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE dialogpilot_app.conversation_projection_outbox outbox
                    SET claimed_by=%s, lease_until=%s, attempt=attempt+1
                    FROM candidates, dialogpilot_app.conversation_events events
                    WHERE outbox.outbox_id=candidates.outbox_id
                      AND events.event_id=outbox.event_id
                    RETURNING outbox.*, events.event_type, events.payload
                """, (
                    projection_name.value, now, now, limit, worker_id, lease_until,
                )).fetchall()
        return tuple(self._event(row) for row in rows)

    def acknowledge(
        self,
        event: ProjectableConversationEvent,
        *,
        worker_id: str,
        outcome: ProjectionOutboxOutcome,
        acknowledged_at: str,
        deletion_epoch: int,
    ) -> None:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                UPDATE dialogpilot_app.conversation_projection_outbox
                SET acknowledged_at=%s, outcome=%s,
                    claimed_by=NULL, lease_until=NULL, last_error_code=NULL
                WHERE outbox_id=%s AND acknowledged_at IS NULL AND claimed_by=%s
                  AND attempt=%s
                RETURNING event_seq
            """, (
                acknowledged_at, outcome.value, event.outbox_id, worker_id,
                event.attempt,
            )).fetchone()
            if row is None:
                existing = connection.execute(
                    "SELECT acknowledged_at, outcome FROM "
                    "dialogpilot_app.conversation_projection_outbox "
                    "WHERE outbox_id=%s", (event.outbox_id,),
                ).fetchone()
                if existing and existing[0] is not None:
                    return
                raise ProjectionClaimError("projection lease is not owned")
            connection.execute("""
                INSERT INTO dialogpilot_app.projection_watermarks (
                    projection_name, generation, tenant_id, user_id,
                    conversation_id, last_event_seq, last_event_id,
                    source_deletion_epoch, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (
                    projection_name, generation, tenant_id, user_id, conversation_id
                ) DO UPDATE SET
                    last_event_seq=GREATEST(
                        dialogpilot_app.projection_watermarks.last_event_seq,
                        EXCLUDED.last_event_seq
                    ),
                    last_event_id=CASE WHEN
                        EXCLUDED.last_event_seq >=
                            dialogpilot_app.projection_watermarks.last_event_seq
                        THEN EXCLUDED.last_event_id
                        ELSE dialogpilot_app.projection_watermarks.last_event_id END,
                    source_deletion_epoch=GREATEST(
                        dialogpilot_app.projection_watermarks.source_deletion_epoch,
                        EXCLUDED.source_deletion_epoch
                    ),
                    updated_at=EXCLUDED.updated_at
            """, (
                event.projection_name.value, event.generation,
                event.subject.tenant_id, event.subject.user_id,
                event.subject.conversation_id, event.event_seq, event.event_id,
                deletion_epoch, acknowledged_at,
            ))

    def renew(
        self,
        event: ProjectableConversationEvent,
        *,
        worker_id: str,
        now: str,
        lease_until: str,
    ) -> None:
        """Renew the current claim epoch without reviving an expired lease."""
        with self.pool.transaction() as connection:
            updated = connection.execute("""
                UPDATE dialogpilot_app.conversation_projection_outbox
                SET lease_until=%s
                WHERE outbox_id=%s AND acknowledged_at IS NULL
                  AND claimed_by=%s AND attempt=%s AND lease_until > %s
            """, (
                lease_until, event.outbox_id, worker_id, event.attempt, now,
            )).rowcount
        if updated != 1:
            raise ProjectionClaimError("projection lease is not owned")

    def release(
        self,
        event: ProjectableConversationEvent,
        *,
        worker_id: str,
        available_at: str,
        error_code: str,
    ) -> None:
        with self.pool.transaction() as connection:
            updated = connection.execute("""
                UPDATE dialogpilot_app.conversation_projection_outbox
                SET claimed_by=NULL, lease_until=NULL, available_at=%s,
                    last_error_code=%s
                WHERE outbox_id=%s AND acknowledged_at IS NULL AND claimed_by=%s
                  AND attempt=%s
            """, (
                available_at, error_code[:200], event.outbox_id, worker_id,
                event.attempt,
            )).rowcount
        if updated != 1:
            raise ProjectionClaimError("projection lease is not owned")

    def rebuild(
        self,
        projection_name: ProjectionName,
        *,
        policy_version: str,
        requested_at: str,
    ) -> int:
        with self.pool.transaction() as connection:
            generation = connection.execute("""
                UPDATE dialogpilot_app.projection_registry
                SET generation=generation+1, policy_version=%s, updated_at=%s
                WHERE projection_name=%s
                RETURNING generation
            """, (
                policy_version, requested_at, projection_name.value,
            )).fetchone()
            if generation is None:
                raise ProjectionClaimError("projection is not registered")
            target_generation = int(generation[0])
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_projection_outbox (
                    outbox_id, projection_name, generation, event_id,
                    tenant_id, user_id, conversation_id, event_seq,
                    source_deletion_epoch, available_at, created_at
                )
                SELECT
                    'projection/' || %s || '/g' || %s || '/' || event_id,
                    %s, %s, event_id, tenant_id, user_id, conversation_id, seq,
                    deletion_epoch, %s, %s
                FROM dialogpilot_app.conversation_events
                ON CONFLICT DO NOTHING
            """, (
                projection_name.value, target_generation,
                projection_name.value, target_generation,
                requested_at, requested_at,
            ))
            return target_generation

    @staticmethod
    def _event(row: Mapping) -> ProjectableConversationEvent:
        return ProjectableConversationEvent(
            outbox_id=row["outbox_id"],
            projection_name=ProjectionName(row["projection_name"]),
            generation=int(row["generation"]),
            event_id=row["event_id"], event_seq=int(row["event_seq"]),
            event_type=row["event_type"], payload=dict(row["payload"]),
            source_deletion_epoch=int(row["source_deletion_epoch"]),
            subject=ConversationSubject(
                row["tenant_id"], row["user_id"], row["conversation_id"],
            ),
            attempt=int(row["attempt"]),
        )


class ConversationProjectionDispatcher:
    def __init__(
        self,
        *,
        outbox: PostgresConversationProjectionOutbox,
        deletion: PostgresConversationDeletionRepository,
        adapters: Mapping[ProjectionName, ConversationProjectionAdapter],
        policy: ConversationProjectionPolicyV1 | None = None,
        fault_hook: Callable[[str], None] | None = None,
        delete_result_originals=None,
    ):
        self.outbox = outbox
        self.deletion = deletion
        self.adapters = dict(adapters)
        self.policy = policy or ConversationProjectionPolicyV1()
        self.fault_hook = fault_hook or (lambda _stage: None)
        self.delete_result_originals = delete_result_originals

    def dispatch_once(
        self,
        *,
        projection_name: ProjectionName,
        worker_id: str,
        now: str,
        lease_until: str,
        retry_at: str,
        limit: int = 20,
    ) -> tuple[ProjectionDispatchResult, ...]:
        if self.delete_result_originals is not None:
            raise ProjectionClaimError("result-original cleanup requires dispatch_once_async")
        adapter = self.adapters.get(projection_name)
        if adapter is None:
            raise ProjectionClaimError(
                f"projection adapter is unavailable: {projection_name.value}"
            )
        claimed = self.outbox.claim(
            projection_name=projection_name, worker_id=worker_id,
            now=now, lease_until=lease_until, limit=limit,
        )
        results = []
        for event in claimed:
            acknowledged = False
            result = None
            try:
                result = self._dispatch(event, adapter)
                self.fault_hook("after_projection_effect")
                self.outbox.acknowledge(
                    event, worker_id=worker_id, outcome=result,
                    acknowledged_at=now,
                    deletion_epoch=self.deletion.fence(event.subject).deletion_epoch,
                )
                acknowledged = True
                self.fault_hook("after_projection_ack")
                results.append(ProjectionDispatchResult(event.outbox_id, result.value))
            except Exception as exc:
                if acknowledged:
                    results.append(ProjectionDispatchResult(
                        event.outbox_id, result.value, type(exc).__name__,
                    ))
                    continue
                try:
                    self.outbox.release(
                        event, worker_id=worker_id, available_at=retry_at,
                        error_code=type(exc).__name__,
                    )
                except ProjectionClaimError:
                    pass
                results.append(ProjectionDispatchResult(
                    event.outbox_id, "RETRY", type(exc).__name__,
                ))
        return tuple(results)

    async def dispatch_once_async(
        self,
        *,
        projection_name: ProjectionName,
        worker_id: str,
        now: str,
        lease_until: str,
        retry_at: str,
        limit: int = 20,
    ) -> tuple[ProjectionDispatchResult, ...]:
        """Apply async projections on their owning event loop; keep PostgreSQL I/O off it."""
        import asyncio

        adapter = self.adapters.get(projection_name)
        if adapter is None:
            raise ProjectionClaimError(
                f"projection adapter is unavailable: {projection_name.value}"
            )
        claimed = await asyncio.to_thread(
            self.outbox.claim,
            projection_name=projection_name, worker_id=worker_id,
            now=now, lease_until=lease_until, limit=limit,
        )
        results = []
        for event in claimed:
            acknowledged = False
            result = None
            try:
                result = await self._dispatch_async(event, adapter)
                self.fault_hook("after_projection_effect")
                fence = await asyncio.to_thread(self.deletion.fence, event.subject)
                await asyncio.to_thread(
                    self.outbox.acknowledge,
                    event, worker_id=worker_id, outcome=result,
                    acknowledged_at=now, deletion_epoch=fence.deletion_epoch,
                )
                acknowledged = True
                self.fault_hook("after_projection_ack")
                results.append(ProjectionDispatchResult(event.outbox_id, result.value))
            except Exception as exc:
                if acknowledged:
                    results.append(ProjectionDispatchResult(
                        event.outbox_id, result.value, type(exc).__name__,
                    ))
                    continue
                try:
                    await asyncio.to_thread(
                        self.outbox.release,
                        event, worker_id=worker_id, available_at=retry_at,
                        error_code=type(exc).__name__,
                    )
                except ProjectionClaimError:
                    pass
                results.append(ProjectionDispatchResult(
                    event.outbox_id, "RETRY", type(exc).__name__,
                ))
        return tuple(results)

    async def _dispatch_async(
        self,
        event: ProjectableConversationEvent,
        adapter: ConversationProjectionAdapter,
    ) -> ProjectionOutboxOutcome:
        import asyncio

        before = await asyncio.to_thread(self.deletion.fence, event.subject)
        if (
            event.event_type == "CONVERSATION_DELETED"
            or before.deleted
            or before.deletion_epoch != event.source_deletion_epoch
        ):
            await self._delete_async(adapter, event.subject, before.deletion_epoch)
            return ProjectionOutboxOutcome.DELETION_FENCED
        if not self.policy.allows(event):
            return ProjectionOutboxOutcome.POLICY_SKIPPED
        apply_async = getattr(adapter, "apply_async", None)
        applied = (
            await apply_async(event)
            if apply_async is not None
            else await asyncio.to_thread(adapter.apply, event)
        )
        after = await asyncio.to_thread(self.deletion.fence, event.subject)
        if after.deleted or after.deletion_epoch != event.source_deletion_epoch:
            await self._delete_async(adapter, event.subject, after.deletion_epoch)
            return ProjectionOutboxOutcome.DELETION_FENCED
        return (
            ProjectionOutboxOutcome.APPLIED
            if applied is ProjectionApplyStatus.APPLIED
            else ProjectionOutboxOutcome.ALREADY_APPLIED
        )

    async def _delete_async(self, adapter, subject, deletion_epoch: int) -> None:
        import asyncio

        if self.delete_result_originals is not None:
            await self.delete_result_originals(subject)
        delete_async = getattr(adapter, "delete_subject_async", None)
        if delete_async is not None:
            await delete_async(
                subject, through_deletion_epoch=deletion_epoch,
            )
            return
        await asyncio.to_thread(
            adapter.delete_subject,
            subject, through_deletion_epoch=deletion_epoch,
        )

    def _dispatch(
        self,
        event: ProjectableConversationEvent,
        adapter: ConversationProjectionAdapter,
    ) -> ProjectionOutboxOutcome:
        before = self.deletion.fence(event.subject)
        if (
            event.event_type == "CONVERSATION_DELETED"
            or before.deleted
            or before.deletion_epoch != event.source_deletion_epoch
        ):
            adapter.delete_subject(
                event.subject, through_deletion_epoch=before.deletion_epoch,
            )
            return ProjectionOutboxOutcome.DELETION_FENCED
        if not self.policy.allows(event):
            return ProjectionOutboxOutcome.POLICY_SKIPPED
        applied = adapter.apply(event)
        after = self.deletion.fence(event.subject)
        if after.deleted or after.deletion_epoch != event.source_deletion_epoch:
            adapter.delete_subject(
                event.subject, through_deletion_epoch=after.deletion_epoch,
            )
            return ProjectionOutboxOutcome.DELETION_FENCED
        return (
            ProjectionOutboxOutcome.APPLIED
            if applied is ProjectionApplyStatus.APPLIED
            else ProjectionOutboxOutcome.ALREADY_APPLIED
        )


def _stable_id(namespace: str, value: str) -> str:
    return f"{namespace}:v1:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
