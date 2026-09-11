"""Separate durable planning admission from single-writer conversation execution."""
from alembic import op

revision = "20260911_0039"
down_revision = "20260911_0038"
branch_labels = None
depends_on = None
subject_linked_write = False
data_location_ids = ()


def upgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM dialogpilot_app.compatibility_execution_outbox
                       WHERE acknowledged_at IS NULL AND attempts > 0) THEN
                RAISE EXCEPTION 'Drain/reconcile started Target Runs before migrating execution ownership';
            END IF;
        END $$;
        ALTER TABLE dialogpilot_app.compatibility_execution_outbox
        ADD COLUMN execution_phase text NOT NULL DEFAULT 'PLANNING'
            CHECK (execution_phase IN ('PLANNING', 'READY', 'EXECUTING')),
        ADD COLUMN execution_started_at timestamptz;
        CREATE UNIQUE INDEX target_conversation_execution_owner
        ON dialogpilot_app.compatibility_execution_outbox
            (tenant_id, user_id, conversation_id)
        WHERE acknowledged_at IS NULL AND execution_phase = 'EXECUTING';
    """)


def downgrade():
    raise RuntimeError("Run ownership migration is forward-only")
