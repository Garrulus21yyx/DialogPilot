"""M4-T01 Memory read projection over PostgreSQL source watermarks."""
from __future__ import annotations

from datetime import timezone

from application.memory_projection import (
    MemoryProjectionResult,
    MemoryProjectionState,
    MemoryRetrievalOutcome,
    ProjectionRange,
)
from memory.conversation_memory import MemoryContext, Message, MsgRole


_TARGETS = (
    "working_window", "thread_summary", "fact_extraction",
)


class PostgresMemoryProjectionReader:
    def __init__(self, pool, memory):
        self.pool = pool
        self.memory = memory

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

        diagnostics: dict = {}
        current_context_reader = getattr(self.memory, "get_current_context", None)
        try:
            if current_context_reader is not None:
                context = await current_context_reader(
                    user_id, conv_id, diagnostics=diagnostics,
                )
                cross_session_requested = False
            else:
                context = await self.memory.get_context(
                    user_id, conv_id, query=query, diagnostics=diagnostics,
                )
                cross_session_requested = True
            memory_available = True
        except Exception as exc:
            context = empty
            memory_available = False
            cross_session_requested = current_context_reader is None
            diagnostics["failures"] = [f"MEMORY_READ_{type(exc).__name__}"]

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
        failures = tuple(dict.fromkeys(diagnostics.get("failures") or ()))
        retrieval_outcome = self._retrieval_outcome(
            query, context, failures, conflicts,
            cross_session_requested=cross_session_requested,
        )

        raw_fallback = False
        if omitted or not memory_available:
            try:
                recent = self._raw_turns(
                    tenant_id, user_id, conv_id,
                    current_request_id=current_request_id,
                )
                context = MemoryContext(
                    recent_messages=recent,
                    relevant_history=(
                        context.relevant_history if memory_available else []
                    ),
                    user_profile=(context.user_profile if memory_available else {}),
                    summary=(context.summary if memory_available else ""),
                    retrieval_hits=(
                        context.retrieval_hits if memory_available else []
                    ),
                )
                raw_fallback = True
            except Exception as exc:
                failures = (*failures, f"RAW_FALLBACK_{type(exc).__name__}")

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
    ) -> list[Message]:
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT role, content, created_at, metadata, turn_id, seq
                FROM dialogpilot_app.conversation_turns
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND (%s='' OR request_id IS DISTINCT FROM %s)
                ORDER BY seq DESC LIMIT 200
            """, (
                tenant_id, user_id, conv_id, current_request_id, current_request_id,
            )).fetchall()
        return [Message(
            role=MsgRole.USER if role == "inbound" else MsgRole.ASSISTANT,
            content=content,
            timestamp=created_at.astimezone(timezone.utc),
            metadata={**dict(metadata or {}), "raw_source_fallback": True},
            message_id=turn_id,
            seq=int(seq),
        ) for role, content, created_at, metadata, turn_id, seq in reversed(rows)]

    @staticmethod
    def _retrieval_outcome(
        query, context, failures, conflicts, *, cross_session_requested=True,
    ):
        if conflicts:
            return MemoryRetrievalOutcome.CONFLICT
        if not cross_session_requested:
            return MemoryRetrievalOutcome.NOT_NEEDED
        retrieval_failures = [
            item for item in failures if "EPISODIC" in item or "MEMORY_READ" in item
        ]
        if retrieval_failures:
            return MemoryRetrievalOutcome.UNAVAILABLE
        if not str(query or "").strip():
            return MemoryRetrievalOutcome.NOT_NEEDED
        return (
            MemoryRetrievalOutcome.HITS
            if context.retrieval_hits else MemoryRetrievalOutcome.NO_MATCH
        )
