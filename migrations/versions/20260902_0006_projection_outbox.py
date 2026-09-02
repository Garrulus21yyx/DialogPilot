"""Create event projection outbox, watermarks and deletion epoch fence.

Revision ID: 20260902_0006
Revises: 20260902_0005
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0006"
down_revision: Union[str, Sequence[str], None] = "20260902_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE dialogpilot_app.conversations
        ADD COLUMN deletion_epoch BIGINT NOT NULL DEFAULT 0
            CHECK (deletion_epoch >= 0)
    """)
    op.execute("""
        ALTER TABLE dialogpilot_app.conversation_events
        ADD COLUMN deletion_epoch BIGINT NOT NULL DEFAULT 0
            CHECK (deletion_epoch >= 0)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.projection_registry (
            projection_name TEXT PRIMARY KEY,
            generation BIGINT NOT NULL CHECK(generation >= 1),
            policy_version TEXT NOT NULL,
            location_id TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
        )
    """)
    op.execute("""
        INSERT INTO dialogpilot_app.projection_registry (
            projection_name, generation, policy_version, location_id
        ) VALUES
            ('working_window', 1, 'conversation-projection-v1',
             'location:redis-working-window:v1'),
            ('thread_summary', 1, 'conversation-projection-v1',
             'location:thread-summary:v1'),
            ('fact_extraction', 1, 'conversation-projection-v1',
             'location:fact-extraction:v1')
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.conversation_projection_outbox (
            outbox_id TEXT PRIMARY KEY,
            projection_name TEXT NOT NULL REFERENCES
                dialogpilot_app.projection_registry(projection_name),
            generation BIGINT NOT NULL CHECK(generation >= 1),
            event_id TEXT NOT NULL REFERENCES
                dialogpilot_app.conversation_events(event_id),
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            event_seq BIGINT NOT NULL CHECK(event_seq > 0),
            source_deletion_epoch BIGINT NOT NULL CHECK(source_deletion_epoch >= 0),
            available_at TIMESTAMPTZ NOT NULL,
            claimed_by TEXT,
            lease_until TIMESTAMPTZ,
            attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
            acknowledged_at TIMESTAMPTZ,
            outcome TEXT CHECK(outcome IN (
                'APPLIED', 'ALREADY_APPLIED', 'POLICY_SKIPPED', 'DELETION_FENCED'
            )),
            last_error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (projection_name, generation, event_id),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id),
            CHECK (
                (claimed_by IS NULL AND lease_until IS NULL)
                OR (claimed_by IS NOT NULL AND lease_until IS NOT NULL)
            ),
            CHECK (
                (acknowledged_at IS NULL AND outcome IS NULL)
                OR (acknowledged_at IS NOT NULL AND outcome IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX conversation_projection_due_idx
        ON dialogpilot_app.conversation_projection_outbox (
            projection_name, generation, acknowledged_at, available_at,
            lease_until, tenant_id, user_id, conversation_id, event_seq
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.projection_watermarks (
            projection_name TEXT NOT NULL,
            generation BIGINT NOT NULL,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            last_event_seq BIGINT NOT NULL CHECK(last_event_seq >= 0),
            last_event_id TEXT,
            source_deletion_epoch BIGINT NOT NULL CHECK(source_deletion_epoch >= 0),
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (
                projection_name, generation, tenant_id, user_id, conversation_id
            ),
            FOREIGN KEY (projection_name)
                REFERENCES dialogpilot_app.projection_registry(projection_name),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.conversation_deletion_events (
            deletion_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            prior_epoch BIGINT NOT NULL CHECK(prior_epoch >= 0),
            target_epoch BIGINT NOT NULL CHECK(target_epoch = prior_epoch + 1),
            reason_code TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (tenant_id, user_id, conversation_id, target_epoch),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE TRIGGER conversation_deletion_events_immutable
        BEFORE UPDATE ON dialogpilot_app.conversation_deletion_events
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_platform.guard_conversation_write()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            tombstoned_at TIMESTAMPTZ;
            current_epoch BIGINT;
        BEGIN
            SELECT deleted_at, deletion_epoch INTO tombstoned_at, current_epoch
            FROM dialogpilot_app.conversations
            WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id
              AND conversation_id=NEW.conversation_id;
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'conversation_events' THEN
                NEW.deletion_epoch := current_epoch;
                IF NEW.event_type = 'CONVERSATION_DELETED' THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF tombstoned_at IS NOT NULL THEN
                RAISE EXCEPTION 'conversation is deletion-fenced'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    for table in (
        "conversation_turns", "conversation_events", "response_deliveries",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_deletion_fence
            BEFORE INSERT ON dialogpilot_app.{table}
            FOR EACH ROW EXECUTE FUNCTION
                dialogpilot_platform.guard_conversation_write()
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_platform.enqueue_conversation_projection()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO dialogpilot_app.conversation_projection_outbox (
                outbox_id, projection_name, generation, event_id,
                tenant_id, user_id, conversation_id, event_seq,
                source_deletion_epoch, available_at, created_at
            )
            SELECT
                'projection/' || registry.projection_name || '/g' ||
                    registry.generation || '/' || NEW.event_id,
                registry.projection_name, registry.generation, NEW.event_id,
                NEW.tenant_id, NEW.user_id, NEW.conversation_id, NEW.seq,
                NEW.deletion_epoch, NEW.created_at, transaction_timestamp()
            FROM dialogpilot_app.projection_registry registry
            WHERE registry.enabled;
            RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER conversation_event_projection_outbox
        AFTER INSERT ON dialogpilot_app.conversation_events
        FOR EACH ROW EXECUTE FUNCTION
            dialogpilot_platform.enqueue_conversation_projection()
    """)
    op.execute("""
        INSERT INTO dialogpilot_app.conversation_projection_outbox (
            outbox_id, projection_name, generation, event_id,
            tenant_id, user_id, conversation_id, event_seq,
            source_deletion_epoch, available_at, created_at
        )
        SELECT
            'projection/' || registry.projection_name || '/g' ||
                registry.generation || '/' || events.event_id,
            registry.projection_name, registry.generation, events.event_id,
            events.tenant_id, events.user_id, events.conversation_id, events.seq,
            events.deletion_epoch, events.created_at, transaction_timestamp()
        FROM dialogpilot_app.conversation_events events
        CROSS JOIN dialogpilot_app.projection_registry registry
        WHERE registry.enabled
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    raise RuntimeError("projection/deletion downgrade requires forward-fix or restore")
