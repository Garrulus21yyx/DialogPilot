"""Fail-open persistence sink for already-sanitized application spans."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging

from psycopg.types.json import Jsonb


logger = logging.getLogger(__name__)


class PostgresTraceSink:
    def __init__(self, pool, *, retention_days: int = 7):
        self.pool = pool
        self.retention_days = max(1, int(retention_days))

    @contextmanager
    def span(self, handle):
        try:
            yield
        finally:
            try:
                with self.pool.transaction() as connection:
                    connection.execute("""
                        INSERT INTO dialogpilot_app.trace_spans (
                            trace_id,span_id,parent_span_id,name,kind,status,
                            started_at,duration_ms,attributes,error_type
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(trace_id,span_id) DO NOTHING
                    """, (
                        handle.trace_id, handle.span_id, handle.parent_span_id,
                        handle.name, handle.kind, handle.status,
                        datetime.fromtimestamp(handle.started_at, timezone.utc),
                        handle.duration_ms, Jsonb(handle.attributes), handle.error_type,
                    ))
                    connection.execute("""
                        DELETE FROM dialogpilot_app.trace_spans
                        WHERE started_at < transaction_timestamp()-(%s*interval '1 day')
                    """, (self.retention_days,))
            except Exception:
                logger.warning(
                    "trace persistence failed trace_id=%s span_id=%s",
                    handle.trace_id, handle.span_id, exc_info=True,
                )

    def close(self) -> None:
        pass

    def get_trace(self, trace_id: str) -> list[dict]:
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT trace_id,span_id,parent_span_id,name,kind,status,
                       started_at,duration_ms,attributes,error_type
                FROM dialogpilot_app.trace_spans
                WHERE trace_id=%s ORDER BY started_at,span_id
            """, (trace_id,)).fetchall()
        return [{
            "trace_id": row[0], "span_id": row[1], "parent_span_id": row[2],
            "name": row[3], "kind": row[4], "status": row[5],
            "started_at": row[6].isoformat(), "duration_ms": float(row[7]),
            "attributes": dict(row[8]), "error_type": row[9],
        } for row in rows]
