"""Add immutable Knowledge SourceRevision v0 and generation manifests.

Revision ID: 20260902_0012
Revises: 20260902_0011
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0012"
down_revision: Union[str, Sequence[str], None] = "20260902_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids = (
    "location:knowledge-source:v1",
    "location:knowledge-index:v1",
)

REGISTRY_VERSION = "v3"
REGISTRY_FINGERPRINT = "14bda1d84d888883c2d4f42bfbc0b88c04860441c0d243b2b01e3a9cdfc98ade"


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version, artifact_fingerprint, artifact_path, approved_by
        ) VALUES (
            '{REGISTRY_VERSION}', '{REGISTRY_FINGERPRINT}',
            'governance/data_locations/v3.json',
            'knowledge-source-lifecycle-review'
        )
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_source_revisions (
            tenant_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            checksum TEXT NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
            title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            content TEXT NOT NULL,
            effective_from TIMESTAMPTZ NOT NULL,
            effective_to TIMESTAMPTZ,
            immutable_fingerprint TEXT NOT NULL
                CHECK(immutable_fingerprint ~ '^[0-9a-f]{64}$'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id, source_id, revision_id),
            UNIQUE (tenant_id, source_id, checksum),
            CHECK(source_id !~ '^legacy-' AND revision_id !~ '^legacy-'),
            CHECK(length(btrim(content)) > 0),
            CHECK(effective_to IS NULL OR effective_to > effective_from)
        )
    """)
    op.execute("""
        CREATE TRIGGER knowledge_source_revisions_immutable
        BEFORE UPDATE ON retrieval.knowledge_source_revisions
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_source_manifests (
            tenant_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            corpus TEXT GENERATED ALWAYS AS ('KNOWLEDGE') STORED,
            scope TEXT NOT NULL,
            locale TEXT NOT NULL,
            product TEXT NOT NULL DEFAULT '',
            manifest_hash TEXT NOT NULL CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
            source_count INTEGER NOT NULL CHECK(source_count > 0),
            schema_version TEXT NOT NULL CHECK(schema_version='knowledge-source-v0'),
            reviewer_manifest_ref TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (tenant_id, backend_id, generation_id, scope, locale, product),
            FOREIGN KEY (corpus, backend_id, generation_id)
                REFERENCES retrieval.retrieval_generation_registry
                (corpus, backend_id, generation_id)
        )
    """)
    op.execute("""
        CREATE TABLE retrieval.knowledge_source_manifest_entries (
            tenant_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            locale TEXT NOT NULL,
            product TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            checksum TEXT NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
            PRIMARY KEY (
                tenant_id, backend_id, generation_id, scope, locale, product,
                source_id, revision_id
            ),
            FOREIGN KEY (tenant_id, backend_id, generation_id, scope, locale, product)
                REFERENCES retrieval.knowledge_source_manifests
                (tenant_id, backend_id, generation_id, scope, locale, product),
            FOREIGN KEY (tenant_id, source_id, revision_id)
                REFERENCES retrieval.knowledge_source_revisions
                (tenant_id, source_id, revision_id)
        )
    """)
    for table in (
        "knowledge_source_manifests", "knowledge_source_manifest_entries",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE ON retrieval.{table}
            FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION retrieval.guard_knowledge_manifest_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE generation_state TEXT;
        BEGIN
            SELECT state INTO generation_state
            FROM retrieval.retrieval_generation_registry
            WHERE corpus='KNOWLEDGE' AND backend_id=NEW.backend_id
              AND generation_id=NEW.generation_id;
            IF generation_state IS DISTINCT FROM 'BUILDING' THEN
                RAISE EXCEPTION 'knowledge manifest writes require BUILDING generation'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END
        $$
    """)
    for table in (
        "knowledge_source_manifests", "knowledge_source_manifest_entries",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_generation_fence
            BEFORE INSERT ON retrieval.{table}
            FOR EACH ROW EXECUTE FUNCTION retrieval.guard_knowledge_manifest_insert()
        """)
    op.execute("""
        GRANT SELECT ON
            retrieval.knowledge_source_revisions,
            retrieval.knowledge_source_manifests,
            retrieval.knowledge_source_manifest_entries
        TO dialogpilot_retrieval
    """)


def downgrade() -> None:
    raise RuntimeError("Knowledge SourceRevision downgrade requires forward-fix")
