"""Add PostgreSQL-owned handoff tickets, events and delivery outbox.

Revision ID: 20260902_0024
Revises: 20260902_0023
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0024"
down_revision: Union[str, Sequence[str], None] = "20260902_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.handoff_tickets (
            ticket_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            question TEXT NOT NULL,
            published_response TEXT NOT NULL,
            reason TEXT NOT NULL,
            priority TEXT NOT NULL CHECK(priority IN ('normal','high','critical')),
            status TEXT NOT NULL CHECK(status IN (
                'open','in_progress','waiting_customer','resolved','closed'
            )),
            agent_type TEXT NOT NULL,
            intent TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            assignee TEXT,
            identity_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            version BIGINT NOT NULL DEFAULT 1 CHECK(version >= 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
        )
    """)
    op.execute("""
        CREATE INDEX handoff_tickets_user_updated_idx
        ON dialogpilot_app.handoff_tickets(user_id,updated_at DESC,ticket_id)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.handoff_ticket_events (
            event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ticket_id TEXT NOT NULL REFERENCES dialogpilot_app.handoff_tickets(ticket_id)
                ON DELETE CASCADE,
            from_status TEXT,
            to_status TEXT NOT NULL,
            actor TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.handoff_ticket_outbox (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            ticket_id TEXT NOT NULL REFERENCES dialogpilot_app.handoff_tickets(ticket_id)
                ON DELETE CASCADE,
            payload JSONB NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            available_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            claimed_by TEXT,
            lease_until TIMESTAMPTZ,
            last_error TEXT,
            delivered_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
        )
    """)
    op.execute("""
        CREATE INDEX handoff_ticket_outbox_due_idx
        ON dialogpilot_app.handoff_ticket_outbox(delivered_at,available_at,created_at)
    """)


def downgrade() -> None:
    raise RuntimeError("Handoff tickets use forward-only migrations")
