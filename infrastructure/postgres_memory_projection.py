"""M4-T01 Memory read projection over PostgreSQL source watermarks."""
from __future__ import annotations

from datetime import timezone
import json

from application.memory_projection import (
    MemoryProjectionResult,
    MemoryProjectionState,
    MemoryRetrievalOutcome,
    ProjectionRange,
)
from memory.conversation_memory import MemoryContext, Message, MsgRole
from application.conversation_projection import ConversationSubject
from infrastructure.postgres_thread_summary import PostgresThreadSummaryRepository


_TARGETS = (
    "working_window", "thread_summary", "fact_extraction",
)


class PostgresMemoryProjectionReader:
    def __init__(self, pool):
        self.pool = pool
        self._summaries = PostgresThreadSummaryRepository(pool)

    async def get_projection_result(
        self,
        tenant_id: str,
        user_id: str,
        conv_id: str,
        *,
        query: str = "",
        current_request_id: str = "",
    ) -> MemoryProjectionResult:
        empty = MemoryContext([], [], {}, "", [])
        try:
            source, watermarks = self._watermarks(tenant_id, user_id, conv_id)
        except Exception as exc:
            return MemoryProjectionResult(
                MemoryProjectionState.UNAVAILABLE,
                empty,
                0,
                {},
                MemoryRetrievalOutcome.UNAVAILABLE,
                reason_codes=(f"SOURCE_WATERMARK_{type(exc).__name__}",),
            )

        failures = ()
        try:
            view = self._summaries.read(ConversationSubject(tenant_id, user_id, conv_id))
            if view.conflicts or view.state.value in {"DEGRADED", "UNAVAILABLE"}:
                raise ValueError("thread summary source is not verified")
            context = MemoryContext([], [], {},
                json.dumps({"summaries": view.summaries}, ensure_ascii=False) if view.summaries else "", [],
                summary_covered_until_seq=view.projection_watermark if view.summaries else 0)
            memory_available = True
        except Exception as exc:
            context = empty
            memory_available = False
            failures = (f"SUMMARY_READ_{type(exc).__name__}",)

        conflicts = tuple(
            f"{name}:watermark_ahead_of_source"
            for name, value in watermarks.items() if value > source
        )
        omitted = tuple(
            ProjectionRange(name, value + 1, source, "projection_lag")
            for name, value in watermarks.items() if value < source
        )
        included = tuple(
            ProjectionRange(name, 1, min(value, source), "projected")
            for name, value in watermarks.items() if min(value, source) > 0
        )
        retrieval_outcome = self._retrieval_outcome(conflicts)

        raw_fallback = False
        # Transcript is the one current-message source, including when Redis
        # reports READY. Its bounded working window is not summary coverage.
        try:
            covered = context.summary_covered_until_seq if context.summary else 0
            if covered > source:
                context = empty
                covered = 0
                failures = (*failures, "SUMMARY_COVERAGE_AHEAD_OF_SOURCE")
            recent = self._raw_turns(
                tenant_id, user_id, conv_id, current_request_id=current_request_id,
                covered_until_seq=covered, source_watermark=source,
            )
            context = MemoryContext(
                recent_messages=recent,
                relevant_history=context.relevant_history,
                user_profile=context.user_profile,
                summary=context.summary,
                summary_covered_until_seq=covered,
                retrieval_hits=context.retrieval_hits,
            )
            raw_fallback = bool(omitted or not memory_available or failures)
        except Exception as exc:
            context = empty
            memory_available = False
            failures = (*failures, f"TRANSCRIPT_READ_{type(exc).__name__}")

        if conflicts or failures:
            state = MemoryProjectionState.DEGRADED
        elif omitted:
            state = MemoryProjectionState.LAGGING
        else:
            state = MemoryProjectionState.READY
        if not memory_available and not raw_fallback:
            state = MemoryProjectionState.UNAVAILABLE
        return MemoryProjectionResult(
            state,
            context,
            source,
            watermarks,
            retrieval_outcome,
            included_ranges=included,
            omitted_ranges=omitted,
            conflicts=conflicts,
            reason_codes=failures,
            raw_fallback_used=raw_fallback,
        )

    def _watermarks(self, tenant_id: str, user_id: str, conv_id: str):
        with self.pool.transaction() as connection:
            source = connection.execute("""
                SELECT COALESCE(max(seq), 0)
                FROM dialogpilot_app.conversation_events
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (tenant_id, user_id, conv_id)).fetchone()[0]
            rows = connection.execute("""
                SELECT registry.projection_name,
                       COALESCE(watermark.last_event_seq, 0)
                FROM dialogpilot_app.projection_registry registry
                LEFT JOIN dialogpilot_app.projection_watermarks watermark
                  ON watermark.projection_name=registry.projection_name
                 AND watermark.generation=registry.generation
                 AND watermark.tenant_id=%s AND watermark.user_id=%s
                 AND watermark.conversation_id=%s
                WHERE registry.enabled=TRUE
                  AND registry.projection_name=ANY(%s)
            """, (tenant_id, user_id, conv_id, list(_TARGETS))).fetchall()
        values = {str(name): int(value) for name, value in rows}
        for name in _TARGETS:
            values.setdefault(name, 0)
        return int(source), values

    def _raw_turns(
        self, tenant_id: str, user_id: str, conv_id: str, *, current_request_id: str,
        covered_until_seq: int = 0, source_watermark: int,
    ) -> list[Message]:
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                WITH dialogue AS (
                    SELECT turn.role, turn.content, turn.created_at, turn.metadata,
                           turn.turn_id, event.seq,
                           row_number() OVER (ORDER BY event.seq DESC) AS recent_rank
                    FROM dialogpilot_app.conversation_events event
                    JOIN dialogpilot_app.conversation_turns turn
                      ON turn.turn_key=COALESCE(event.payload->>'inbound_turn_key',
                                                event.payload->>'outbound_turn_key')
                    WHERE event.tenant_id=%s AND event.user_id=%s AND event.conversation_id=%s
                      AND (%s='' OR turn.request_id IS DISTINCT FROM %s)
                      AND event.seq <= %s
                )
                SELECT role, content, created_at, metadata, turn_id, seq
                FROM dialogue WHERE seq > %s OR recent_rank <= 8
                ORDER BY seq DESC
            """, (
                tenant_id, user_id, conv_id, current_request_id, current_request_id,
                source_watermark, covered_until_seq,
            )).fetchall()
        return [Message(
            role=MsgRole.USER if role == "inbound" else MsgRole.ASSISTANT,
            content=content,
            timestamp=created_at.astimezone(timezone.utc),
            metadata={**dict(metadata or {}), "source": "committed_transcript"},
            message_id=turn_id,
            seq=int(seq),
        ) for role, content, created_at, metadata, turn_id, seq in reversed(rows)]

    @staticmethod
    def _retrieval_outcome(conflicts):
        if conflicts:
            return MemoryRetrievalOutcome.CONFLICT
        return MemoryRetrievalOutcome.NOT_NEEDED
