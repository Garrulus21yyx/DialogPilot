"""Add a bounded local persistence surface for sanitized application spans.

Revision ID: 20260902_0026
Revises: 20260902_0025
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0026"
down_revision: Union[str, Sequence[str], None] = "20260902_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.trace_spans (
            trace_id TEXT NOT NULL,
            span_id TEXT NOT NULL,
            parent_span_id TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ok','error')),
            started_at TIMESTAMPTZ NOT NULL,
            duration_ms DOUBLE PRECISION NOT NULL CHECK(duration_ms >= 0),
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_type TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY(trace_id,span_id)
        )
    """)
    op.execute("""
        CREATE INDEX trace_spans_started_idx
        ON dialogpilot_app.trace_spans(started_at DESC,trace_id,span_id)
    """)


def downgrade() -> None:
    raise RuntimeError("Trace persistence uses forward-only migrations")
