"""Make the retrieval runtime role read-only over rebuildable search projections.

Revision ID: 20260902_0015
Revises: 20260902_0014
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0015"
down_revision: Union[str, Sequence[str], None] = "20260902_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = (
    "location:knowledge-index:v1",
    "location:episode-index:v1",
)


def upgrade() -> None:
    op.execute("""
        REVOKE INSERT, UPDATE, DELETE ON
            retrieval.knowledge_chunk_search,
            retrieval.service_episode_search
        FROM dialogpilot_retrieval
    """)


def downgrade() -> None:
    raise RuntimeError("retrieval projection write-boundary downgrade requires forward-fix")
