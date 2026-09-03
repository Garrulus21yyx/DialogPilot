"""Preserve canonical ServiceEpisode entity scope for dense projection.

Revision ID: 20260903_0028
Revises: 20260903_0027
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0028"
down_revision: Union[str, Sequence[str], None] = "20260903_0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = True
data_location_ids = (
    "location:service-episode:v1",
    "location:episode-index:v1",
)


def upgrade() -> None:
    # Historical rows predate entity-scoped retrieval and truthfully retain an
    # empty set.  New writes must supply the canonical entity IDs explicitly.
    op.execute("""
        ALTER TABLE dialogpilot_app.service_episode_revisions
        ADD COLUMN entity_ids TEXT[] NOT NULL DEFAULT '{}'
    """)
    op.execute("""
        ALTER TABLE dialogpilot_app.service_episode_revisions
        ALTER COLUMN entity_ids DROP DEFAULT
    """)
    op.execute("""
        CREATE INDEX service_episode_search_entity_ids_gin
        ON retrieval.service_episode_search USING GIN (entity_ids)
    """)


def downgrade() -> None:
    raise RuntimeError("ServiceEpisode entity scope uses a forward-only migration")
