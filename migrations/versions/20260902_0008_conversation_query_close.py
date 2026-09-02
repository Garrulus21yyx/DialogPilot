"""Add audited conversation close state for M1 query projections.

Revision ID: 20260902_0008
Revises: 20260902_0007
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0008"
down_revision: Union[str, Sequence[str], None] = "20260902_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = (
    "location:pg-conversation-facts:v1",
    "location:pg-projection-control:v1",
)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE dialogpilot_app.conversations
        ADD COLUMN closed_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.conversation_close_events (
            close_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, user_id, conversation_id),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE TRIGGER conversation_close_events_immutable
        BEFORE UPDATE ON dialogpilot_app.conversation_close_events
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_platform.guard_conversation_write()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            tombstoned_at TIMESTAMPTZ;
            conversation_closed_at TIMESTAMPTZ;
            current_epoch BIGINT;
        BEGIN
            SELECT deleted_at, closed_at, deletion_epoch
                INTO tombstoned_at, conversation_closed_at, current_epoch
            FROM dialogpilot_app.conversations
            WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
              AND conversation_id=NEW.conversation_id;
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'conversation_events' THEN
                NEW.deletion_epoch := current_epoch;
                IF NEW.event_type IN (
                    'CONVERSATION_DELETED', 'CONVERSATION_CLOSED'
                ) THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF tombstoned_at IS NOT NULL THEN
                RAISE EXCEPTION 'conversation is deletion-fenced'
                    USING ERRCODE = '55000';
            END IF;
            IF conversation_closed_at IS NOT NULL THEN
                RAISE EXCEPTION 'conversation is closed'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$
    """)


def downgrade() -> None:
    raise RuntimeError("conversation close downgrade requires forward-fix")
