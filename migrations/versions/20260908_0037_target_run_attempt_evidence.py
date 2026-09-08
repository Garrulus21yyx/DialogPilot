"""Retain Target run attempt failures in the existing execution outbox."""
from alembic import op

revision = "20260908_0037"
down_revision = "20260906_0036"
branch_labels = None
depends_on = None
subject_linked_write = True
data_location_ids = ("location:pg-workflow-outboxes:v1",)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE dialogpilot_app.compatibility_execution_outbox
        ADD COLUMN attempt_failures JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(attempt_failures) = 'object'),
        ADD COLUMN selected_failure JSONB
        CHECK (selected_failure IS NULL OR jsonb_typeof(selected_failure) = 'object')
    """)


def downgrade() -> None:
    raise RuntimeError("Attempt evidence requires a forward migration")
