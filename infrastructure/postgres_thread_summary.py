"""PostgreSQL owner for fixed-range ThreadSummary jobs and checkpoint CAS."""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.conversation_projection import ConversationSubject
from application.thread_summary import (
    SummarySourceItem,
    ThreadSummaryApplyStatus,
    ThreadSummaryConflict,
    ThreadSummaryJob,
    ThreadSummaryView,
)


class PostgresThreadSummaryRepository:
    def __init__(self, pool, *, fault_hook: Callable[[str], None] | None = None):
        self.pool = pool
        self.fault_hook = fault_hook or (lambda _stage: None)

    def prepare(
        self,
        subject: ConversationSubject,
        *,
        summarizer_version: str,
        max_events: int = 50,
    ) -> ThreadSummaryJob | None:
        if not summarizer_version.strip() or max_events < 1 or max_events > 500:
            raise ValueError("invalid thread summary policy")
        with self.pool.transaction() as connection:
            conversation = connection.execute("""
                SELECT deletion_epoch, deleted_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(subject)).fetchone()
            if conversation is None or conversation[1] is not None:
                raise ThreadSummaryConflict("thread summary subject is unavailable")
            checkpoint = connection.execute("""
                SELECT generation, projection_watermark, expected_version
                FROM dialogpilot_app.thread_summary_checkpoints
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(subject)).fetchone()
            generation, prior, version = (
                (int(checkpoint[0]), int(checkpoint[1]), int(checkpoint[2]))
                if checkpoint else (1, 0, 0)
            )
            source_watermark = int(connection.execute("""
                SELECT COALESCE(max(seq), 0) FROM dialogpilot_app.conversation_events
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(subject)).fetchone()[0])
            if prior >= source_watermark:
                return None
            pending_metrics = connection.execute("""
                SELECT count(*) FILTER (WHERE turn.content IS NOT NULL),
                       COALESCE(sum(
                           CASE WHEN turn.content IS NOT NULL
                           THEN GREATEST(1, CEIL(char_length(turn.content) / 4.0))
                           ELSE 0 END
                       ), 0)
                FROM dialogpilot_app.conversation_events event
                LEFT JOIN dialogpilot_app.conversation_turns turn
                  ON turn.turn_key=COALESCE(event.payload->>'inbound_turn_key',
                                            event.payload->>'outbound_turn_key')
                WHERE event.tenant_id=%s AND event.user_id=%s
                  AND event.conversation_id=%s AND event.seq > %s
            """, (*_scope(subject), prior)).fetchone()
            rows = self._source_rows(
                connection, subject, prior + 1,
                min(source_watermark, prior + max_events),
            )
        items = tuple(_item(row) for row in rows)
        source_hash = _source_hash(items)
        included = _ranges(item.seq for item in items if item.content)
        omitted = _ranges(item.seq for item in items if not item.content)
        start, end = items[0].seq, items[-1].seq
        return ThreadSummaryJob(
            job_id=_stable(
                "summary-job", subject, generation, start, end, source_hash,
                summarizer_version,
            ),
            subject=subject, generation=generation, from_seq=start, to_seq=end,
            source_watermark=source_watermark, source_sha256=source_hash,
            source_deletion_epoch=int(conversation[0]), expected_version=version,
            items=items, included_ranges=included, omitted_ranges=omitted,
            pending_message_count=int(pending_metrics[0]),
            pending_token_estimate=int(pending_metrics[1]),
            summarizer_version=summarizer_version,
        )

    def commit(
        self, job: ThreadSummaryJob, *, summary: str,
    ) -> ThreadSummaryApplyStatus:
        if not summary.strip():
            raise ValueError("thread summary candidate is blank")
        chunk_id = _stable(
            "summary-chunk", job.subject, job.generation, job.from_seq,
            job.to_seq, job.source_sha256, job.summarizer_version,
            job.schema_version,
        )
        with self.pool.transaction() as connection:
            conversation = connection.execute("""
                SELECT 1 FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, _scope(job.subject)).fetchone()
            if conversation is None:
                raise ThreadSummaryConflict("thread summary subject is unavailable")
            current = connection.execute("""
                SELECT generation, projection_watermark, expected_version,
                       source_deletion_epoch
                FROM dialogpilot_app.thread_summary_checkpoints
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, _scope(job.subject)).fetchone()
            generation, prior, version, epoch = (
                tuple(map(int, current)) if current
                else (1, 0, 0, job.source_deletion_epoch)
            )
            existing = connection.execute("""
                SELECT source_sha256, summary FROM dialogpilot_app.thread_summary_chunks
                WHERE chunk_id=%s
            """, (chunk_id,)).fetchone()
            if existing is not None:
                if existing != (job.source_sha256, summary):
                    raise ThreadSummaryConflict("thread summary replay conflicts")
                if prior >= job.to_seq:
                    return ThreadSummaryApplyStatus.ALREADY_APPLIED
            if (generation, prior, version, epoch) != (
                job.generation, job.from_seq - 1, job.expected_version,
                job.source_deletion_epoch,
            ):
                raise ThreadSummaryConflict("thread summary checkpoint CAS mismatch")
            items = tuple(_item(row) for row in self._source_rows(
                connection, job.subject, job.from_seq, job.to_seq,
            ))
            if _source_hash(items) != job.source_sha256:
                raise ThreadSummaryConflict("thread summary source changed")
            source_watermark = int(connection.execute("""
                SELECT COALESCE(max(seq),0) FROM dialogpilot_app.conversation_events
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(job.subject)).fetchone()[0])
            connection.execute("""
                INSERT INTO dialogpilot_app.thread_summary_chunks (
                    chunk_id, tenant_id, user_id, conversation_id, generation,
                    from_seq, to_seq, source_sha256, summary, included_ranges,
                    omitted_ranges, summarizer_version, schema_version,
                    source_deletion_epoch
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                chunk_id, *_scope(job.subject), job.generation, job.from_seq,
                job.to_seq, job.source_sha256, summary,
                Jsonb(list(job.included_ranges)), Jsonb(list(job.omitted_ranges)),
                job.summarizer_version, job.schema_version,
                job.source_deletion_epoch,
            ))
            self.fault_hook("after_summary_chunk")
            state = "READY" if job.to_seq == source_watermark else "LAGGING"
            checkpoint_write = connection.execute("""
                INSERT INTO dialogpilot_app.thread_summary_checkpoints (
                    tenant_id,user_id,conversation_id,generation,source_watermark,
                    projection_watermark,last_chunk_id,state,expected_version,
                    source_deletion_epoch
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,user_id,conversation_id) DO UPDATE SET
                    source_watermark=EXCLUDED.source_watermark,
                    projection_watermark=EXCLUDED.projection_watermark,
                    last_chunk_id=EXCLUDED.last_chunk_id,state=EXCLUDED.state,
                    expected_version=EXCLUDED.expected_version,
                    updated_at=transaction_timestamp()
                WHERE thread_summary_checkpoints.expected_version=%s
            """, (
                *_scope(job.subject), job.generation, source_watermark, job.to_seq,
                chunk_id, state, job.expected_version + 1,
                job.source_deletion_epoch, job.expected_version,
            ))
            if checkpoint_write.rowcount != 1:
                raise ThreadSummaryConflict("thread summary checkpoint CAS lost")
            self.fault_hook("after_summary_checkpoint")
        return ThreadSummaryApplyStatus.APPLIED

    def mark_degraded(self, job: ThreadSummaryJob, *, error_code: str) -> None:
        if not error_code.strip():
            raise ValueError("thread summary degradation code is required")
        with self.pool.transaction() as connection:
            conversation = connection.execute("""
                SELECT deletion_epoch, deleted_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, _scope(job.subject)).fetchone()
            if conversation is None or conversation[1] is not None:
                raise ThreadSummaryConflict("thread summary subject is unavailable")
            if int(conversation[0]) != job.source_deletion_epoch:
                raise ThreadSummaryConflict("thread summary degradation is deletion-fenced")
            source_watermark = int(connection.execute("""
                SELECT COALESCE(max(seq),0) FROM dialogpilot_app.conversation_events
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(job.subject)).fetchone()[0])
            current = connection.execute("""
                SELECT generation,projection_watermark,expected_version,last_chunk_id
                FROM dialogpilot_app.thread_summary_checkpoints
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, _scope(job.subject)).fetchone()
            if current and tuple(map(int, current[:3])) != (
                job.generation, job.from_seq - 1, job.expected_version,
            ):
                raise ThreadSummaryConflict("thread summary degradation CAS mismatch")
            last_chunk_id = current[3] if current else None
            connection.execute("""
                INSERT INTO dialogpilot_app.thread_summary_checkpoints (
                    tenant_id,user_id,conversation_id,generation,source_watermark,
                    projection_watermark,last_chunk_id,state,expected_version,
                    source_deletion_epoch
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'DEGRADED',%s,%s)
                ON CONFLICT (tenant_id,user_id,conversation_id) DO UPDATE SET
                    source_watermark=EXCLUDED.source_watermark,state='DEGRADED',
                    updated_at=transaction_timestamp()
            """, (
                *_scope(job.subject), job.generation, source_watermark,
                job.from_seq - 1, last_chunk_id, job.expected_version,
                job.source_deletion_epoch,
            ))

    def start_rebuild(self, subject: ConversationSubject) -> int:
        with self.pool.transaction() as connection:
            conversation = connection.execute("""
                SELECT deletion_epoch, deleted_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, _scope(subject)).fetchone()
            if conversation is None or conversation[1] is not None:
                raise ThreadSummaryConflict("thread summary subject is unavailable")
            source_watermark = int(connection.execute("""
                SELECT COALESCE(max(seq),0) FROM dialogpilot_app.conversation_events
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(subject)).fetchone()[0])
            current = connection.execute("""
                SELECT generation,expected_version
                FROM dialogpilot_app.thread_summary_checkpoints
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR UPDATE
            """, _scope(subject)).fetchone()
            # Even an implicit empty checkpoint is generation=1/version=0.
            # Explicit rebuild must fence jobs prepared before this transaction.
            generation = int(current[0]) + 1 if current else 2
            version = int(current[1]) + 1 if current else 1
            state = "READY" if source_watermark == 0 else "LAGGING"
            connection.execute("""
                INSERT INTO dialogpilot_app.thread_summary_checkpoints (
                    tenant_id,user_id,conversation_id,generation,source_watermark,
                    projection_watermark,last_chunk_id,state,expected_version,
                    source_deletion_epoch
                ) VALUES (%s,%s,%s,%s,%s,0,NULL,%s,%s,%s)
                ON CONFLICT (tenant_id,user_id,conversation_id) DO UPDATE SET
                    generation=EXCLUDED.generation,
                    source_watermark=EXCLUDED.source_watermark,
                    projection_watermark=0,last_chunk_id=NULL,
                    state=EXCLUDED.state,expected_version=EXCLUDED.expected_version,
                    source_deletion_epoch=EXCLUDED.source_deletion_epoch,
                    updated_at=transaction_timestamp()
            """, (
                *_scope(subject), generation, source_watermark, state, version,
                int(conversation[0]),
            ))
        return generation

    def read(self, subject: ConversationSubject) -> ThreadSummaryView:
        with self.pool.transaction() as connection:
            conversation = connection.execute("""
                SELECT deleted_at FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(subject)).fetchone()
            if conversation is None or conversation[0] is not None:
                return ThreadSummaryView("UNAVAILABLE", 1, 0, 0, 0, (), (), ())
            actual_source_watermark = int(connection.execute("""
                SELECT COALESCE(max(seq),0) FROM dialogpilot_app.conversation_events
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(subject)).fetchone()[0])
            checkpoint = connection.execute("""
                SELECT generation,source_watermark,projection_watermark,
                       expected_version,state,last_chunk_id
                FROM dialogpilot_app.thread_summary_checkpoints
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, _scope(subject)).fetchone()
            if checkpoint is None:
                state = "READY" if actual_source_watermark == 0 else "LAGGING"
                return ThreadSummaryView(
                    state, 1, actual_source_watermark, 0, 0, (), (), (),
                )
            rows = connection.execute("""
                SELECT chunk_id,from_seq,to_seq,source_sha256,summary,
                       included_ranges,omitted_ranges
                FROM dialogpilot_app.thread_summary_chunks
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND generation=%s ORDER BY from_seq
            """, (*_scope(subject), checkpoint[0])).fetchall()
            conflicts = []
            expected = 1
            for row in rows:
                if int(row[1]) != expected:
                    conflicts.append(f"range_gap:{expected}-{int(row[1])-1}")
                try:
                    items = tuple(_item(item) for item in self._source_rows(
                        connection, subject, int(row[1]), int(row[2]),
                    ))
                except ThreadSummaryConflict:
                    conflicts.append(f"raw_unavailable:{int(row[1])}-{int(row[2])}")
                else:
                    if _source_hash(items) != row[3]:
                        conflicts.append(f"source_hash:{int(row[1])}-{int(row[2])}")
                expected = int(row[2]) + 1
            projection_watermark = int(checkpoint[2])
            if projection_watermark and (
                not rows or rows[-1][0] != checkpoint[5]
                or int(rows[-1][2]) != projection_watermark
            ):
                conflicts.append("checkpoint_target")
            if int(checkpoint[1]) > actual_source_watermark:
                conflicts.append("checkpoint_source_watermark")
            state = checkpoint[4]
            if not conflicts and state != "DEGRADED":
                state = (
                    "READY" if projection_watermark == actual_source_watermark
                    else "LAGGING"
                )
        return ThreadSummaryView(
            "DEGRADED" if conflicts else state, int(checkpoint[0]),
            actual_source_watermark, projection_watermark, int(checkpoint[3]),
            tuple(row[4] for row in rows),
            tuple(item for row in rows for item in row[5]),
            tuple(item for row in rows for item in row[6]), tuple(conflicts),
        )

    @staticmethod
    def _source_rows(connection, subject, start, end):
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute("""
                SELECT event.seq,event.event_id,event.event_type,event.content_sha256,
                       turn.role,turn.content
                FROM dialogpilot_app.conversation_events event
                LEFT JOIN dialogpilot_app.conversation_turns turn
                  ON turn.turn_key=COALESCE(event.payload->>'inbound_turn_key',
                                            event.payload->>'outbound_turn_key')
                WHERE event.tenant_id=%s AND event.user_id=%s
                  AND event.conversation_id=%s AND event.seq BETWEEN %s AND %s
                ORDER BY event.seq
            """, (*_scope(subject), start, end)).fetchall()
        if len(rows) != end - start + 1:
            raise ThreadSummaryConflict("thread summary source range has a gap")
        return rows


def _scope(subject):
    return (subject.tenant_id, subject.user_id, subject.conversation_id)


def _item(row):
    return SummarySourceItem(
        int(row["seq"]), row["event_id"], row["event_type"],
        row["content_sha256"], row["role"] or "", row["content"] or "",
    )


def _source_hash(items):
    raw = [item.__dict__ for item in items]
    return hashlib.sha256(json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _ranges(values):
    result = []
    for value in values:
        if result and value == result[-1][1] + 1:
            result[-1] = (result[-1][0], value)
        else:
            result.append((value, value))
    return tuple(result)


def _stable(namespace, subject, *parts):
    raw = ["dialogpilot.thread-summary.v1", namespace, *_scope(subject), *parts]
    return f"{namespace}:v1:{hashlib.sha256(json.dumps(raw, separators=(',', ':')).encode()).hexdigest()}"
