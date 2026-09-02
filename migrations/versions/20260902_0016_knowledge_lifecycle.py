"""Add direct-ingest SourceRevision provenance fields.

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
            ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local-admin',
            ADD COLUMN scope TEXT NOT NULL DEFAULT 'public',
            ADD COLUMN locale TEXT NOT NULL DEFAULT 'zh-CN',
            ADD COLUMN product TEXT NOT NULL DEFAULT '',
            ADD COLUMN region TEXT NOT NULL DEFAULT 'local',
            ADD COLUMN supersedes_revision_id TEXT,
            ADD COLUMN operations_audit_ref TEXT NOT NULL DEFAULT 'local-direct-ingest',
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


def downgrade() -> None:
    raise RuntimeError("Knowledge SourceRevision downgrade requires forward-fix")
