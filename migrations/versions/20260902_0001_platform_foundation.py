"""Create platform namespaces and immutable migration ledger.

Revision ID: 20260902_0001
Revises: None
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS dialogpilot_platform")
    op.execute("CREATE SCHEMA IF NOT EXISTS dialogpilot_app")
    op.execute("""
        CREATE TABLE dialogpilot_platform.migration_ledger (
            revision TEXT PRIMARY KEY,
            file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            applied_by TEXT NOT NULL,
            application_version TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_platform.data_migration_ledger (
            migration_id TEXT NOT NULL,
            object_name TEXT NOT NULL,
            phase TEXT NOT NULL CHECK (phase IN (
                'snapshot', 'backfill', 'shadow_read', 'freeze_old_writer',
                'final_delta', 'reconcile', 'binding_switch', 'new_writer',
                'restore'
            )),
            source_snapshot_id TEXT NOT NULL,
            source_high_watermark TEXT NOT NULL,
            source_count BIGINT NOT NULL CHECK (source_count >= 0),
            source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
            target_count BIGINT CHECK (target_count >= 0),
            target_sha256 TEXT CHECK (
                target_sha256 IS NULL OR length(target_sha256) = 64
            ),
            binding_version TEXT,
            status TEXT NOT NULL CHECK (status IN ('recorded', 'matched', 'failed')),
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            recorded_by TEXT NOT NULL,
            PRIMARY KEY (migration_id, phase)
        )
    """)


def downgrade() -> None:
    raise RuntimeError(
        "platform foundation downgrade is destructive; use forward-fix or restore"
    )
