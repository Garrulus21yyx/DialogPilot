"""Add canonical publication evidence, delivery lifecycle and send outbox.

Revision ID: 20260902_0004
Revises: 20260902_0003
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0004"
down_revision: Union[str, Sequence[str], None] = "20260902_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM dialogpilot_app.response_deliveries) THEN
                RAISE EXCEPTION
                    '20260902_0004 requires an empty pre-cutover response_deliveries table';
            END IF;
        END
        $$
    """)
    op.execute("""
        ALTER TABLE dialogpilot_app.response_deliveries
        DROP CONSTRAINT response_deliveries_status_check
    """)
    op.execute("""
        ALTER TABLE dialogpilot_app.response_deliveries
        ADD CONSTRAINT response_deliveries_status_check CHECK (status IN (
            'SELECTED', 'DELIVERING', 'DELIVERED', 'OUTCOME_UNKNOWN',
            'DELIVERY_UNCERTAIN', 'FAILED', 'READ'
        )),
        ADD COLUMN command_fingerprint TEXT NOT NULL
            CHECK (length(command_fingerprint) = 64),
        ADD COLUMN outbound_turn_key TEXT NOT NULL REFERENCES
            dialogpilot_app.conversation_turns(turn_key),
        ADD COLUMN outbound_event_id TEXT NOT NULL REFERENCES
            dialogpilot_app.conversation_events(event_id),
        ADD COLUMN candidate_id TEXT,
        ADD COLUMN verifier_status TEXT,
        ADD COLUMN verification JSONB,
        ADD COLUMN evidence_sha256 TEXT CHECK (
            evidence_sha256 IS NULL OR length(evidence_sha256) = 64
        ),
        ADD COLUMN bundle_version TEXT,
        ADD COLUMN index_manifest_sha256 TEXT CHECK (
            index_manifest_sha256 IS NULL OR length(index_manifest_sha256) = 64
        ),
        ADD COLUMN signal_id TEXT,
        ADD COLUMN signal_version BIGINT CHECK (signal_version >= 0),
        ADD COLUMN resume_binding_signature TEXT,
        ADD COLUMN ticket_id TEXT,
        ADD COLUMN handoff_id TEXT,
        ADD COLUMN human_message_id TEXT,
        ADD COLUMN connector_capability TEXT NOT NULL CHECK (
            connector_capability IN ('IDEMPOTENT_SEND', 'QUERY_RECEIPT', 'NONE')
        ),
        ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
        ADD COLUMN max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
        ADD COLUMN next_attempt_at TIMESTAMPTZ,
        ADD COLUMN retry_policy_version TEXT NOT NULL,
        ADD COLUMN reconcile_deadline TIMESTAMPTZ NOT NULL,
        ADD COLUMN last_error_code TEXT,
        ADD CONSTRAINT response_delivery_kind_contract CHECK (
            (publication_kind = 'final_response'
             AND invocation_key IS NOT NULL AND candidate_id IS NOT NULL
             AND verifier_status IS NOT NULL AND evidence_sha256 IS NOT NULL
             AND bundle_version IS NOT NULL AND index_manifest_sha256 IS NOT NULL)
            OR
            (publication_kind = 'interaction_request'
             AND invocation_key IS NOT NULL AND signal_id IS NOT NULL
             AND signal_version IS NOT NULL AND resume_binding_signature IS NOT NULL)
            OR
            (publication_kind = 'human_reply'
             AND ticket_id IS NOT NULL AND handoff_id IS NOT NULL
             AND human_message_id IS NOT NULL)
        ),
        ADD CONSTRAINT response_delivery_attempt_contract CHECK (
            attempt <= max_attempts
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX response_delivery_final_invocation_uidx
        ON dialogpilot_app.response_deliveries(invocation_key)
        WHERE publication_kind='final_response'
    """)
    op.execute("""
        CREATE UNIQUE INDEX response_delivery_signal_uidx
        ON dialogpilot_app.response_deliveries(signal_id, signal_version)
        WHERE publication_kind='interaction_request'
    """)
    op.execute("""
        CREATE UNIQUE INDEX response_delivery_human_message_uidx
        ON dialogpilot_app.response_deliveries(ticket_id, handoff_id, human_message_id)
        WHERE publication_kind='human_reply'
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.delivery_outbox (
            outbox_id TEXT PRIMARY KEY,
            publication_id TEXT NOT NULL UNIQUE REFERENCES
                dialogpilot_app.response_deliveries(publication_id),
            delivery_operation_key TEXT NOT NULL UNIQUE,
            payload JSONB NOT NULL,
            available_at TIMESTAMPTZ NOT NULL,
            claimed_by TEXT,
            lease_until TIMESTAMPTZ,
            acknowledged_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            CHECK (
                (claimed_by IS NULL AND lease_until IS NULL)
                OR (claimed_by IS NOT NULL AND lease_until IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX delivery_outbox_due_idx ON dialogpilot_app.delivery_outbox
        (acknowledged_at, available_at, lease_until, created_at)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.delivery_receipts (
            receipt_id TEXT PRIMARY KEY,
            publication_id TEXT NOT NULL REFERENCES
                dialogpilot_app.response_deliveries(publication_id),
            delivery_event TEXT NOT NULL,
            payload JSONB NOT NULL,
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            resulting_status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("""
        CREATE TRIGGER delivery_receipts_immutable
        BEFORE UPDATE ON dialogpilot_app.delivery_receipts
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.publication_revision_events (
            revision_event_id TEXT PRIMARY KEY,
            publication_id TEXT NOT NULL REFERENCES
                dialogpilot_app.response_deliveries(publication_id),
            prior_content_sha256 TEXT NOT NULL CHECK (length(prior_content_sha256) = 64),
            revised_payload JSONB NOT NULL,
            revised_content_sha256 TEXT NOT NULL CHECK (length(revised_content_sha256) = 64),
            reason_code TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("""
        CREATE TRIGGER publication_revisions_immutable
        BEFORE UPDATE ON dialogpilot_app.publication_revision_events
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)


def downgrade() -> None:
    raise RuntimeError("publication/delivery downgrade requires forward-fix or restore")
