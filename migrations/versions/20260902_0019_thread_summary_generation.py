"""Add rebuild generations to thread-summary ownership.

Revision ID: 20260902_0019
Revises: 20260902_0018
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0019"
down_revision: Union[str, Sequence[str], None] = "20260902_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = ("location:thread-summary:v1",)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE dialogpilot_app.thread_summary_chunks
        ADD COLUMN generation BIGINT NOT NULL DEFAULT 1 CHECK(generation >= 1)
    """)
    op.execute("""
        ALTER TABLE dialogpilot_app.thread_summary_checkpoints
        ADD COLUMN generation BIGINT NOT NULL DEFAULT 1 CHECK(generation >= 1)
    """)
    op.execute("""
        DO $$
        DECLARE existing_constraint TEXT;
        BEGIN
            SELECT constraint_name INTO existing_constraint
            FROM information_schema.table_constraints
            WHERE table_schema='dialogpilot_app'
              AND table_name='thread_summary_chunks'
              AND constraint_type='UNIQUE';
            IF existing_constraint IS NULL THEN
                RAISE EXCEPTION 'thread summary range uniqueness is missing';
            END IF;
            EXECUTE format(
                'ALTER TABLE dialogpilot_app.thread_summary_chunks DROP CONSTRAINT %I',
                existing_constraint
            );
        END $$
    """)
    op.execute("""
        ALTER TABLE dialogpilot_app.thread_summary_chunks
        ADD CONSTRAINT thread_summary_range_generation_unique
        UNIQUE (tenant_id, user_id, conversation_id, generation, from_seq, to_seq,
                summarizer_version, schema_version)
    """)


def downgrade() -> None:
    raise RuntimeError("Thread summary generation downgrade requires forward-fix")
