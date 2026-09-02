"""Create the single-owner ResponseDelivery repository binding.

Revision ID: 20260902_0005
Revises: 20260902_0004
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0005"
down_revision: Union[str, Sequence[str], None] = "20260902_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_platform.delivery_repository_binding (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
            state TEXT NOT NULL CHECK (
                state IN ('SQLITE_ACTIVE', 'FROZEN', 'POSTGRES_ACTIVE')
            ),
            generation BIGINT NOT NULL CHECK(generation >= 1),
            freeze_id TEXT,
            snapshot_sha256 TEXT CHECK (
                snapshot_sha256 IS NULL OR length(snapshot_sha256) = 64
            ),
            switched_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL,
            CHECK (
                (state = 'SQLITE_ACTIVE' AND freeze_id IS NULL
                 AND switched_at IS NULL)
                OR (state = 'FROZEN' AND freeze_id IS NOT NULL
                    AND switched_at IS NULL)
                OR (state = 'POSTGRES_ACTIVE' AND freeze_id IS NOT NULL
                    AND snapshot_sha256 IS NOT NULL AND switched_at IS NOT NULL)
            )
        )
    """)
    op.execute("""
        INSERT INTO dialogpilot_platform.delivery_repository_binding (
            singleton, state, generation, updated_at
        ) VALUES (TRUE, 'SQLITE_ACTIVE', 1, transaction_timestamp())
    """)
    op.execute("""
        CREATE TABLE dialogpilot_platform.delivery_binding_events (
            event_id TEXT PRIMARY KEY,
            prior_state TEXT NOT NULL,
            target_state TEXT NOT NULL,
            prior_generation BIGINT NOT NULL,
            target_generation BIGINT NOT NULL,
            freeze_id TEXT,
            snapshot_sha256 TEXT,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("""
        CREATE TRIGGER delivery_binding_events_immutable
        BEFORE UPDATE ON dialogpilot_platform.delivery_binding_events
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)


def downgrade() -> None:
    raise RuntimeError("delivery binding downgrade requires forward-fix or restore")
