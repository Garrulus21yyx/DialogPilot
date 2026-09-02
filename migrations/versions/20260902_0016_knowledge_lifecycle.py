"""Add SourceRevision review lifecycle and Knowledge Publication pointers.

Revision ID: 20260902_0016
Revises: 20260902_0015
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0016"
down_revision: Union[str, Sequence[str], None] = "20260902_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids = (
    "location:knowledge-source:v1",
    "location:knowledge-index:v1",
)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE retrieval.knowledge_source_revisions
            ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'm2-backfill-owner',
            ADD COLUMN scope TEXT NOT NULL DEFAULT 'public',
            ADD COLUMN locale TEXT NOT NULL DEFAULT 'und',
            ADD COLUMN product TEXT NOT NULL DEFAULT '',
            ADD COLUMN region TEXT NOT NULL DEFAULT 'global',
            ADD COLUMN supersedes_revision_id TEXT,
            ADD COLUMN operations_audit_ref TEXT NOT NULL DEFAULT 'm2-source-revision-v0',
            ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'knowledge-source-v0'
                CHECK(schema_version IN ('knowledge-source-v0', 'knowledge-source-v1')),
            ADD FOREIGN KEY (tenant_id, source_id, supersedes_revision_id)
                REFERENCES retrieval.knowledge_source_revisions
                (tenant_id, source_id, revision_id)
    """)
    op.execute("""
        ALTER TABLE retrieval.knowledge_source_revisions
            ALTER COLUMN owner_id DROP DEFAULT,
            ALTER COLUMN scope DROP DEFAULT,
            ALTER COLUMN locale DROP DEFAULT,
            ALTER COLUMN product DROP DEFAULT,
            ALTER COLUMN region DROP DEFAULT,
            ALTER COLUMN operations_audit_ref DROP DEFAULT,
            ALTER COLUMN schema_version DROP DEFAULT
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_source_revision_lifecycle (
            tenant_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'DRAFT','REVIEWED','REJECTED','STAGED','ACTIVE',
                'SUPERSEDED','RETRACTED'
            )),
            version BIGINT NOT NULL CHECK(version > 0),
            reviewer_id TEXT,
            review_reason_code TEXT,
            review_evidence_ref TEXT,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id, source_id, revision_id),
            FOREIGN KEY (tenant_id, source_id, revision_id)
                REFERENCES retrieval.knowledge_source_revisions
                (tenant_id, source_id, revision_id),
            CHECK((reviewer_id IS NULL) = (review_reason_code IS NULL)),
            CHECK((reviewer_id IS NULL) = (review_evidence_ref IS NULL))
        )
    """)
    op.execute("""
        INSERT INTO retrieval.knowledge_source_revision_lifecycle (
            tenant_id, source_id, revision_id, status, version
        ) SELECT tenant_id, source_id, revision_id, 'ACTIVE', 1
          FROM retrieval.knowledge_source_revisions
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_source_revision_audit (
            audit_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            evidence_ref TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            FOREIGN KEY (tenant_id, source_id, revision_id)
                REFERENCES retrieval.knowledge_source_revision_lifecycle
                (tenant_id, source_id, revision_id)
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_source_revision_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(OLD.tenant_id, OLD.source_id, OLD.revision_id)
               IS DISTINCT FROM ROW(NEW.tenant_id, NEW.source_id, NEW.revision_id)
               OR NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'source lifecycle identity/version is immutable'
                    USING ERRCODE='55000';
            END IF;
            IF NOT ((OLD.status='DRAFT' AND NEW.status IN ('REVIEWED','REJECTED')) OR
                    (OLD.status='REVIEWED' AND NEW.status='STAGED') OR
                    (OLD.status='STAGED' AND NEW.status IN ('ACTIVE','REJECTED')) OR
                    (OLD.status='ACTIVE' AND NEW.status IN ('SUPERSEDED','RETRACTED'))) THEN
                RAISE EXCEPTION 'INVALID_TRANSITION'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER knowledge_source_revision_transition_guard
        BEFORE UPDATE ON retrieval.knowledge_source_revision_lifecycle
        FOR EACH ROW EXECUTE FUNCTION retrieval.guard_source_revision_transition()
    """)
    op.execute("""
        CREATE TRIGGER knowledge_source_revision_audit_immutable
        BEFORE UPDATE ON retrieval.knowledge_source_revision_audit
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_publication_pointers (
            tenant_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            pointer_kind TEXT NOT NULL CHECK(pointer_kind IN ('SCOPED_CANARY','GLOBAL')),
            scope TEXT NOT NULL,
            locale TEXT NOT NULL,
            product TEXT NOT NULL,
            region TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            manifest_hash TEXT NOT NULL CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
            previous_generation_id TEXT,
            previous_manifest_hash TEXT,
            version BIGINT NOT NULL CHECK(version > 0),
            gate_id TEXT NOT NULL,
            gate_evidence_sha256 TEXT NOT NULL CHECK(gate_evidence_sha256 ~ '^[0-9a-f]{64}$'),
            changed_by TEXT NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id, backend_id, pointer_kind, scope, locale, product, region),
            FOREIGN KEY (generation_id)
                REFERENCES retrieval.retrieval_generation_registry (generation_id),
            CHECK((pointer_kind='GLOBAL') =
                  (scope='*' AND locale='*' AND product='' AND region='*')),
            CHECK((previous_generation_id IS NULL) = (previous_manifest_hash IS NULL))
        )
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_publication_audit (
            publication_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            pointer_kind TEXT NOT NULL,
            scope TEXT NOT NULL,
            locale TEXT NOT NULL,
            product TEXT NOT NULL,
            region TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            manifest_hash TEXT NOT NULL,
            previous_generation_id TEXT,
            pointer_version BIGINT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('ACTIVATE','ROLLBACK')),
            gate_id TEXT NOT NULL,
            gate_evidence_sha256 TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("""
        CREATE TRIGGER knowledge_publication_audit_immutable
        BEFORE UPDATE ON retrieval.knowledge_publication_audit
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_request_manifest_pins (
            request_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            pointer_kind TEXT NOT NULL,
            scope TEXT NOT NULL,
            locale TEXT NOT NULL,
            product TEXT NOT NULL,
            region TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            manifest_hash TEXT NOT NULL,
            pointer_version BIGINT NOT NULL,
            pinned_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
        )
    """)
    op.execute("""
        CREATE TRIGGER knowledge_request_manifest_pins_immutable
        BEFORE UPDATE ON retrieval.knowledge_request_manifest_pins
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_candidates (
            candidate_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('NEGATIVE_FEEDBACK','SUPPORT_TICKET','ZERO_HIT')),
            sanitized_payload JSONB NOT NULL CHECK(jsonb_typeof(sanitized_payload)='object'),
            payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
            source_ref TEXT NOT NULL,
            privacy_review_ref TEXT NOT NULL,
            schema_version TEXT NOT NULL CHECK(schema_version='knowledge-candidate-v1'),
            created_at TIMESTAMPTZ NOT NULL,
            CHECK(length(btrim(source_ref)) > 0),
            CHECK(length(btrim(privacy_review_ref)) > 0)
        )
    """)
    op.execute("""
        CREATE TRIGGER knowledge_candidates_immutable
        BEFORE UPDATE ON retrieval.knowledge_candidates
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)


def downgrade() -> None:
    raise RuntimeError("Knowledge lifecycle downgrade requires forward-fix")
