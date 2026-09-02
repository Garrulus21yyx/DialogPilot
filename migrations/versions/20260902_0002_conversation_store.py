"""Create immutable conversation, admission and publication facts.

Revision ID: 20260902_0002
Revises: 20260902_0001
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0002"
down_revision: Union[str, Sequence[str], None] = "20260902_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.conversations (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            next_turn_seq BIGINT NOT NULL DEFAULT 1 CHECK (next_turn_seq > 0),
            next_event_seq BIGINT NOT NULL DEFAULT 1 CHECK (next_event_seq > 0),
            next_publication_seq BIGINT NOT NULL DEFAULT 1 CHECK (next_publication_seq > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            retention_until TIMESTAMPTZ,
            deleted_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE INDEX conversations_retention_idx
        ON dialogpilot_app.conversations (retention_until)
        WHERE deleted_at IS NULL AND retention_until IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.conversation_turns (
            turn_key TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            seq BIGINT NOT NULL CHECK (seq > 0),
            role TEXT NOT NULL CHECK (role IN ('inbound', 'assistant', 'human', 'system_event')),
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
            request_id TEXT,
            invocation_key TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL,
            retention_until TIMESTAMPTZ,
            UNIQUE (tenant_id, user_id, conversation_id, seq),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE INDEX conversation_turns_scope_created_idx
        ON dialogpilot_app.conversation_turns
        (tenant_id, user_id, conversation_id, created_at, seq)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.conversation_events (
            event_id TEXT PRIMARY KEY,
            operation_key TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            seq BIGINT NOT NULL CHECK (seq > 0),
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
            request_id TEXT,
            invocation_key TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            retention_until TIMESTAMPTZ,
            UNIQUE (tenant_id, user_id, conversation_id, seq),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE INDEX conversation_events_scope_created_idx
        ON dialogpilot_app.conversation_events
        (tenant_id, user_id, conversation_id, created_at, seq)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.workflow_invocations (
            invocation_key TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            workflow_run_id TEXT NOT NULL UNIQUE,
            continuation_id TEXT NOT NULL,
            inbound_turn_key TEXT NOT NULL UNIQUE,
            request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
            admission_status TEXT NOT NULL CHECK (admission_status IN (
                'START_QUEUED', 'EXECUTION_BOUND', 'EXPIRED_BEFORE_START'
            )),
            pinned_versions JSONB NOT NULL,
            execution_runtime_kind TEXT,
            execution_runtime_version TEXT,
            execution_run_id TEXT,
            terminal_ref JSONB,
            version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            retention_until TIMESTAMPTZ,
            UNIQUE (tenant_id, user_id, conversation_id, request_id),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id),
            FOREIGN KEY (inbound_turn_key)
                REFERENCES dialogpilot_app.conversation_turns(turn_key),
            CHECK (
                (admission_status = 'EXECUTION_BOUND'
                 AND execution_runtime_kind IS NOT NULL
                 AND execution_runtime_version IS NOT NULL
                 AND execution_run_id IS NOT NULL)
                OR
                (admission_status <> 'EXECUTION_BOUND'
                 AND execution_runtime_kind IS NULL
                 AND execution_runtime_version IS NULL
                 AND execution_run_id IS NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX workflow_invocations_scope_created_idx
        ON dialogpilot_app.workflow_invocations
        (tenant_id, user_id, conversation_id, created_at)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.response_deliveries (
            publication_id TEXT PRIMARY KEY,
            publication_kind TEXT NOT NULL CHECK (publication_kind IN (
                'final_response', 'interaction_request', 'human_reply'
            )),
            delivery_operation_key TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            seq BIGINT NOT NULL CHECK (seq > 0),
            invocation_key TEXT,
            payload JSONB NOT NULL,
            content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
            status TEXT NOT NULL CHECK (status IN (
                'SELECTED', 'DELIVERING', 'DELIVERED', 'OUTCOME_UNKNOWN',
                'FAILED', 'READ'
            )),
            channel_receipt JSONB,
            reconciliation JSONB,
            version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0),
            created_at TIMESTAMPTZ NOT NULL,
            delivered_at TIMESTAMPTZ,
            read_at TIMESTAMPTZ,
            retention_until TIMESTAMPTZ,
            UNIQUE (tenant_id, user_id, conversation_id, seq),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id),
            FOREIGN KEY (invocation_key)
                REFERENCES dialogpilot_app.workflow_invocations(invocation_key)
        )
    """)
    op.execute("""
        CREATE INDEX response_deliveries_replay_idx
        ON dialogpilot_app.response_deliveries
        (tenant_id, user_id, conversation_id, seq)
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_platform.reject_immutable_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'immutable fact % cannot be updated', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER conversation_turns_immutable
        BEFORE UPDATE ON dialogpilot_app.conversation_turns
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE TRIGGER conversation_events_immutable
        BEFORE UPDATE ON dialogpilot_app.conversation_events
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)


def downgrade() -> None:
    raise RuntimeError(
        "conversation facts downgrade is destructive; use forward-fix or restore"
    )
