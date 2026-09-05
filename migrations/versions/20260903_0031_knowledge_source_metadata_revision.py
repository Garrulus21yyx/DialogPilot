"""Let v1 Knowledge revisions distinguish retrieval-affecting metadata.

Revision ID: 20260903_0031
Revises: 20260903_0030
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0031"
down_revision: Union[str, Sequence[str], None] = "20260903_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids = ("location:knowledge-source:v1",)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE retrieval.knowledge_source_revisions
        DROP CONSTRAINT knowledge_source_revisions_tenant_id_source_id_checksum_key
    """)
    op.execute("""
        CREATE INDEX knowledge_source_revisions_content_lookup_idx
        ON retrieval.knowledge_source_revisions
        (tenant_id, source_id, checksum)
    """)


def downgrade() -> None:
    raise RuntimeError("metadata-aware Knowledge revisions are forward-only")
