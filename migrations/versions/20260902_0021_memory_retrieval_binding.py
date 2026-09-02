"""Add atomic Memory retrieval policy/backend/corpus binding.

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
            mode TEXT NOT NULL CHECK(mode IN ('SHADOW','PINNED_CANARY','ACTIVE','LEGACY')),
            active_policy_fingerprint TEXT NOT NULL
                CHECK(active_policy_fingerprint ~ '^[0-9a-f]{64}$'),
            active_backend_id TEXT NOT NULL,
            active_backend_generation TEXT NOT NULL,
            active_corpus_generation TEXT NOT NULL,
            previous_policy_fingerprint TEXT NOT NULL
                CHECK(previous_policy_fingerprint ~ '^[0-9a-f]{64}$'),
            previous_backend_id TEXT NOT NULL,
            previous_backend_generation TEXT NOT NULL,
            previous_corpus_generation TEXT NOT NULL,
            candidate_policy_fingerprint TEXT,
            candidate_backend_id TEXT,
            candidate_backend_generation TEXT,
            candidate_corpus_generation TEXT,
            version BIGINT NOT NULL CHECK(version >= 1),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id, corpus),
            CHECK(
                (candidate_policy_fingerprint IS NULL
                 AND candidate_backend_id IS NULL
                 AND candidate_backend_generation IS NULL
                 AND candidate_corpus_generation IS NULL)
                OR
                (candidate_policy_fingerprint ~ '^[0-9a-f]{64}$'
                 AND candidate_backend_id IS NOT NULL
                 AND candidate_backend_generation IS NOT NULL
                 AND candidate_corpus_generation IS NOT NULL)
            ),
            CHECK(
                (mode IN ('SHADOW','PINNED_CANARY')) =
                (candidate_policy_fingerprint IS NOT NULL)
            )
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
