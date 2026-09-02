"""Harden canonical projection event identity and owner-reference enqueue checks.

Revision ID: 20260902_0014
Revises: 20260902_0013
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0014"
down_revision: Union[str, Sequence[str], None] = "20260902_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = ("location:retrieval-projection-outbox:v1",)


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_canonical_projection_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (OLD.event_id, OLD.corpus, OLD.tenant_id, OLD.backend_id,
                OLD.generation_id, OLD.source_ref, OLD.source_revision,
                OLD.source_fingerprint, OLD.subject_user_id,
                OLD.subject_conversation_id, OLD.deletion_epoch)
               IS DISTINCT FROM
               (NEW.event_id, NEW.corpus, NEW.tenant_id, NEW.backend_id,
                NEW.generation_id, NEW.source_ref, NEW.source_revision,
                NEW.source_fingerprint, NEW.subject_user_id,
                NEW.subject_conversation_id, NEW.deletion_epoch) THEN
                RAISE EXCEPTION 'canonical projection event identity is immutable'
                    USING ERRCODE='55000';
            END IF;
            IF NOT (
                OLD.status=NEW.status OR
                (OLD.status='PENDING' AND NEW.status IN ('PROCESSING','REJECTED')) OR
                (OLD.status='PROCESSING' AND NEW.status IN ('APPLIED','REJECTED'))
            ) THEN
                RAISE EXCEPTION 'illegal canonical projection status transition'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER canonical_projection_update_guard
        BEFORE UPDATE ON retrieval.canonical_projection_outbox
        FOR EACH ROW EXECUTE FUNCTION retrieval.guard_canonical_projection_update()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_canonical_projection_enqueue()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,retrieval,dialogpilot_app AS $$
        DECLARE generation_state TEXT; current_epoch BIGINT; tombstoned_at TIMESTAMPTZ;
        BEGIN
            SELECT state INTO generation_state
              FROM retrieval.retrieval_generation_registry
             WHERE corpus=NEW.corpus AND backend_id=NEW.backend_id
               AND generation_id=NEW.generation_id FOR SHARE;
            IF generation_state IS DISTINCT FROM 'BUILDING' THEN
                RAISE EXCEPTION 'canonical projection requires BUILDING generation'
                    USING ERRCODE='55000';
            END IF;
            IF NEW.corpus='KNOWLEDGE' THEN
                PERFORM 1 FROM retrieval.knowledge_source_manifests
                 WHERE tenant_id=NEW.tenant_id AND backend_id=NEW.backend_id
                   AND generation_id=NEW.generation_id
                   AND manifest_hash=NEW.source_ref
                   AND schema_version=NEW.source_revision;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'canonical knowledge source reference is missing'
                        USING ERRCODE='55000';
                END IF;
            ELSE
                SELECT deletion_epoch, deleted_at INTO current_epoch, tombstoned_at
                  FROM dialogpilot_app.conversations
                 WHERE tenant_id=NEW.tenant_id AND user_id=NEW.subject_user_id
                   AND conversation_id=NEW.subject_conversation_id FOR SHARE;
                IF NOT FOUND OR tombstoned_at IS NOT NULL
                   OR current_epoch <> NEW.deletion_epoch THEN
                    RAISE EXCEPTION 'canonical projection subject is deletion-fenced'
                        USING ERRCODE='55000';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)


def downgrade() -> None:
    raise RuntimeError("canonical projection contract downgrade requires forward-fix")
