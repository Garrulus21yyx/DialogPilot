"""Add PostgreSQL-owned customer commitments and versioned events.

Revision ID: 20260902_0025
Revises: 20260902_0024
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0025"
down_revision: Union[str, Sequence[str], None] = "20260902_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.commitments (
            commitment_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            ticket_id TEXT REFERENCES dialogpilot_app.handoff_tickets(ticket_id),
            kind TEXT NOT NULL,
            description TEXT NOT NULL,
            due_at TIMESTAMPTZ NOT NULL,
            owner TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK(source_kind IN ('manual','business_action')),
            source_receipt_ref TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN (
                'scheduled','fulfilled','cancelled','breached',
                'late_fulfilled','escalated','archived'
            )),
            version BIGINT NOT NULL DEFAULT 1 CHECK(version >= 1),
            retention_class TEXT NOT NULL DEFAULT 'support_standard',
            breached_at TIMESTAMPTZ,
            escalated_at TIMESTAMPTZ,
            fulfilled_at TIMESTAMPTZ,
            archived_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            CHECK(source_kind != 'business_action' OR source_receipt_ref IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE INDEX commitments_user_status_due_idx
        ON dialogpilot_app.commitments(user_id,status,due_at,commitment_id)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.commitment_events (
            event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            commitment_id TEXT NOT NULL
                REFERENCES dialogpilot_app.commitments(commitment_id) ON DELETE CASCADE,
            version BIGINT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            actor TEXT NOT NULL,
            receipt_ref TEXT,
            note TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            UNIQUE(commitment_id,version)
        )
    """)


def downgrade() -> None:
    raise RuntimeError("Commitments use forward-only migrations")
