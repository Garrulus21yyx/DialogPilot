"""Add the single direct-cutover Memory retrieval binding.

Revision ID: 20260902_0021
Revises: 20260902_0020
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0021"
down_revision: Union[str, Sequence[str], None] = "20260902_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE retrieval.memory_retrieval_bindings (
            tenant_id TEXT NOT NULL,
            corpus TEXT NOT NULL CHECK(corpus='SERVICE_EPISODE'),
            policy_fingerprint TEXT NOT NULL
                CHECK(policy_fingerprint ~ '^[0-9a-f]{64}$'),
            backend_id TEXT NOT NULL,
            backend_generation TEXT NOT NULL,
            corpus_generation TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            version BIGINT NOT NULL CHECK(version >= 1),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id, corpus)
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_memory_retrieval_binding_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (OLD.tenant_id, OLD.corpus) IS DISTINCT FROM
               (NEW.tenant_id, NEW.corpus) THEN
                RAISE EXCEPTION 'memory retrieval binding identity is immutable'
                    USING ERRCODE='55000';
            END IF;
            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'memory retrieval binding version must increment once'
                    USING ERRCODE='55000';
            END IF;
            IF OLD.enabled AND ROW(
                NEW.policy_fingerprint,NEW.backend_id,NEW.backend_generation,
                NEW.corpus_generation,NEW.enabled
            ) IS DISTINCT FROM ROW(
                OLD.policy_fingerprint,OLD.backend_id,OLD.backend_generation,
                OLD.corpus_generation,TRUE
            ) THEN
                RAISE EXCEPTION 'enabled memory retrieval binding is immutable'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER memory_retrieval_binding_update_guard
        BEFORE UPDATE ON retrieval.memory_retrieval_bindings
        FOR EACH ROW EXECUTE FUNCTION retrieval.guard_memory_retrieval_binding_update()
    """)


def downgrade() -> None:
    raise RuntimeError("Memory retrieval binding downgrade requires forward-fix")
