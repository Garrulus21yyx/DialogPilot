"""Add PostgreSQL-owned versioned user memory facts.

Revision ID: 20260902_0022
Revises: 20260902_0021
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0022"
down_revision: Union[str, Sequence[str], None] = "20260902_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.memory_facts (
            fact_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','superseded','retracted')),
            document JSONB NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            source_conversation_id TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL,
            CHECK(document->>'fact_id'=fact_id),
            CHECK(document->>'user_id'=user_id),
            CHECK(document->>'key'=fact_key),
            CHECK(document->>'status'=status)
        )
    """)
    op.execute("""
        CREATE INDEX memory_facts_user_active_idx
        ON dialogpilot_app.memory_facts(user_id,fact_key,observed_at,fact_id)
        WHERE status='active'
    """)
    op.execute("""
        CREATE INDEX memory_facts_conversation_idx
        ON dialogpilot_app.memory_facts(user_id,source_conversation_id)
        WHERE source_conversation_id<>''
    """)


def downgrade() -> None:
    raise RuntimeError("Memory facts use forward-only migrations")
