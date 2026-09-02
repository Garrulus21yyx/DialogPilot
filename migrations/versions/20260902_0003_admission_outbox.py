"""Create start/resume outboxes for inbound-first admission.

Revision ID: 20260902_0003
Revises: 20260902_0002
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0003"
down_revision: Union[str, Sequence[str], None] = "20260902_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.workflow_start_outbox (
            outbox_id TEXT PRIMARY KEY,
            invocation_key TEXT NOT NULL UNIQUE REFERENCES
                dialogpilot_app.workflow_invocations(invocation_key),
            workflow_run_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            available_at TIMESTAMPTZ NOT NULL,
            claimed_by TEXT,
            lease_until TIMESTAMPTZ,
            last_error TEXT,
            acknowledged_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            CHECK (
                (claimed_by IS NULL AND lease_until IS NULL)
                OR (claimed_by IS NOT NULL AND lease_until IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX workflow_start_outbox_due_idx
        ON dialogpilot_app.workflow_start_outbox
        (acknowledged_at, available_at, lease_until, created_at)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.resume_requested_outbox (
            outbox_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL UNIQUE,
            expected_signal_version BIGINT NOT NULL CHECK (expected_signal_version >= 0),
            workflow_run_id TEXT NOT NULL,
            inbound_turn_key TEXT NOT NULL UNIQUE REFERENCES
                dialogpilot_app.conversation_turns(turn_key),
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            available_at TIMESTAMPTZ NOT NULL,
            claimed_by TEXT,
            lease_until TIMESTAMPTZ,
            last_error TEXT,
            acknowledged_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id),
            CHECK (
                (claimed_by IS NULL AND lease_until IS NULL)
                OR (claimed_by IS NOT NULL AND lease_until IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX resume_requested_outbox_due_idx
        ON dialogpilot_app.resume_requested_outbox
        (acknowledged_at, available_at, lease_until, created_at)
    """)


def downgrade() -> None:
    raise RuntimeError("admission outbox downgrade requires forward-fix or restore")
