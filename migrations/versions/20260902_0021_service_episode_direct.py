"""Finalize direct ServiceEpisode retrieval for fresh installations.

Revision ID: 20260902_0021
Revises: 20260902_0020

The active retrieval generation created by the shared retrieval registry is the
sole runtime selector. No tenant rollout binding is created.
"""
from typing import Sequence, Union


revision: str = "20260902_0021"
down_revision: Union[str, Sequence[str], None] = "20260902_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("ServiceEpisode schema uses forward-only migrations")
