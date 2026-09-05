"""Add contextual Knowledge retrieval text, BM25 terms, and metadata filters.

Revision ID: 20260903_0030
Revises: 20260903_0029
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0030"
down_revision: Union[str, Sequence[str], None] = "20260903_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = ("location:knowledge-index:v1",)


def upgrade() -> None:
    for table in ("knowledge_source_chunk_specs", "knowledge_chunk_search"):
        op.execute(f"""
            ALTER TABLE retrieval.{table}
            ADD COLUMN retrieval_text TEXT NOT NULL DEFAULT '',
            ADD COLUMN section_path TEXT[] NOT NULL DEFAULT '{{}}',
            ADD COLUMN source_type TEXT NOT NULL DEFAULT 'text',
            ADD COLUMN region TEXT NOT NULL DEFAULT 'global'
        """)
    op.execute("""
        ALTER TABLE retrieval.knowledge_chunk_search
        ADD COLUMN lexical_terms TEXT[] GENERATED ALWAYS AS
            (string_to_array(lexical_document, ' ')) STORED
    """)
    op.execute("""
        CREATE INDEX knowledge_chunk_search_lexical_terms_gin
        ON retrieval.knowledge_chunk_search USING GIN (lexical_terms)
    """)
    op.execute("""
        CREATE INDEX knowledge_chunk_search_metadata_idx
        ON retrieval.knowledge_chunk_search
        (tenant_id, generation_id, source_type, region, product)
    """)
    op.execute("""
        GRANT SELECT ON retrieval.knowledge_source_chunk_specs,
            retrieval.knowledge_chunk_search TO dialogpilot_retrieval
    """)


def downgrade() -> None:
    raise RuntimeError("contextual Knowledge retrieval uses a forward-only migration")
