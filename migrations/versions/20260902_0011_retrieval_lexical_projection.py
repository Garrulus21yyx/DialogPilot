"""Make PG_FTS_ZH_V1 tsvector a database-owned generated projection.

Revision ID: 20260902_0011
Revises: 20260902_0010
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0011"
down_revision: Union[str, Sequence[str], None] = "20260902_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = (
    "location:knowledge-index:v1",
    "location:episode-index:v1",
)


def upgrade() -> None:
    for table in ("knowledge_chunk_search", "service_episode_search"):
        op.execute(f"DROP INDEX retrieval.{table}_search_tsv_gin")
        op.execute(f"ALTER TABLE retrieval.{table} DROP COLUMN search_tsv")
        op.execute(f"""
            ALTER TABLE retrieval.{table}
            ADD COLUMN search_tsv TSVECTOR GENERATED ALWAYS AS
                (to_tsvector('simple', lexical_document)) STORED
        """)
        op.execute(f"""
            CREATE INDEX {table}_search_tsv_gin
            ON retrieval.{table} USING GIN (search_tsv)
        """)


def downgrade() -> None:
    raise RuntimeError("retrieval lexical projection downgrade requires forward-fix")
