"""Add the durable M1 compatibility invocation execution outbox.

Revision ID: 20260902_0017
Revises: 20260902_0016
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0017"
down_revision: Union[str, Sequence[str], None] = "20260902_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = ("location:pg-workflow-outboxes:v1",)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.compatibility_execution_outbox (
            job_id TEXT PRIMARY KEY,
            invocation_key TEXT NOT NULL UNIQUE REFERENCES
                dialogpilot_app.workflow_invocations(invocation_key),
            workflow_run_id TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            source_deletion_epoch BIGINT NOT NULL CHECK(source_deletion_epoch >= 0),
            runtime_version TEXT NOT NULL CHECK(length(btrim(runtime_version)) > 0),
            available_at TIMESTAMPTZ NOT NULL,
            claimed_by TEXT,
            lease_until TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            acknowledged_at TIMESTAMPTZ,
            outcome_type TEXT,
            terminal_ref JSONB,
            last_error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            CHECK((claimed_by IS NULL) = (lease_until IS NULL)),
            CHECK((acknowledged_at IS NULL) = (outcome_type IS NULL)),
            CHECK((acknowledged_at IS NULL) = (terminal_ref IS NULL)),
            FOREIGN KEY (tenant_id, user_id, conversation_id)
                REFERENCES dialogpilot_app.conversations
                (tenant_id, user_id, conversation_id)
        )
    """)
    op.execute("""
        CREATE INDEX compatibility_execution_due_idx
        ON dialogpilot_app.compatibility_execution_outbox (
            acknowledged_at, available_at, lease_until, created_at, job_id
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION dialogpilot_platform.guard_compat_execution_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(OLD.job_id, OLD.invocation_key, OLD.workflow_run_id,
                   OLD.tenant_id, OLD.user_id, OLD.conversation_id,
                   OLD.source_deletion_epoch, OLD.runtime_version, OLD.created_at)
               IS DISTINCT FROM
               ROW(NEW.job_id, NEW.invocation_key, NEW.workflow_run_id,
                   NEW.tenant_id, NEW.user_id, NEW.conversation_id,
                   NEW.source_deletion_epoch, NEW.runtime_version, NEW.created_at) THEN
                RAISE EXCEPTION 'compatibility execution identity is immutable'
                    USING ERRCODE='55000';
            END IF;
            IF OLD.acknowledged_at IS NOT NULL AND ROW(
                OLD.acknowledged_at, OLD.outcome_type, OLD.terminal_ref
            ) IS DISTINCT FROM ROW(
                NEW.acknowledged_at, NEW.outcome_type, NEW.terminal_ref
            ) THEN
                RAISE EXCEPTION 'compatibility execution terminal is immutable'
                    USING ERRCODE='55000';
            END IF;
            IF NEW.attempts < OLD.attempts THEN
                RAISE EXCEPTION 'compatibility claim epoch cannot decrease'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER compatibility_execution_update_guard
        BEFORE UPDATE ON dialogpilot_app.compatibility_execution_outbox
        FOR EACH ROW EXECUTE FUNCTION
            dialogpilot_platform.guard_compat_execution_update()
    """)


def downgrade() -> None:
    raise RuntimeError("Compatibility execution downgrade requires forward-fix")
