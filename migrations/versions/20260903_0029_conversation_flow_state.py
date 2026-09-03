"""Add the conversation-scoped active-flow CAS aggregate.

Revision ID: 20260903_0029
Revises: 20260903_0028
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0029"
down_revision: Union[str, Sequence[str], None] = "20260903_0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = ("location:conversation-flow-state:v1",)

REGISTRY_FINGERPRINT = (
    "159e473579c9f157560410142302023de2b39a15e8869b8679cc8e40d16e479f"
)


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version,artifact_fingerprint,artifact_path,approved_by
        ) VALUES (
            'v7','{REGISTRY_FINGERPRINT}',
            'governance/data_locations/v7.json',
            'command-primary-flow-state-owner-review'
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.conversation_flow_state (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            aggregate_id TEXT NOT NULL UNIQUE,
            state JSONB NOT NULL,
            version BIGINT NOT NULL CHECK(version >= 1),
            deletion_epoch BIGINT NOT NULL CHECK(deletion_epoch >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id,user_id,conversation_id),
            FOREIGN KEY (tenant_id,user_id,conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id,user_id,conversation_id),
            CHECK(jsonb_typeof(state)='object'),
            CHECK(state->>'schema_version'='conversation-flow-state-v1')
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_app.guard_conversation_flow_state()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_epoch BIGINT; tombstone TIMESTAMPTZ;
        BEGIN
            SELECT deletion_epoch,deleted_at INTO current_epoch,tombstone
            FROM dialogpilot_app.conversations
            WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
              AND conversation_id=NEW.conversation_id FOR SHARE;
            IF NOT FOUND OR tombstone IS NOT NULL
               OR current_epoch <> NEW.deletion_epoch THEN
                RAISE EXCEPTION 'conversation flow state is deletion-fenced'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER conversation_flow_state_subject_fence
        BEFORE INSERT OR UPDATE ON dialogpilot_app.conversation_flow_state
        FOR EACH ROW EXECUTE FUNCTION
            dialogpilot_app.guard_conversation_flow_state()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_app.purge_flow_state_on_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
                DELETE FROM dialogpilot_app.conversation_flow_state
                WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
                  AND conversation_id=NEW.conversation_id;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER conversation_flow_state_delete
        AFTER UPDATE OF deleted_at ON dialogpilot_app.conversations
        FOR EACH ROW EXECUTE FUNCTION
            dialogpilot_app.purge_flow_state_on_delete()
    """)


def downgrade() -> None:
    raise RuntimeError("conversation flow state uses a forward-only migration")
