"""Reserved revision after direct PostgreSQL ResponseDelivery initialization.

Revision ID: 20260902_0005
Revises: 20260902_0004
"""
from typing import Sequence, Union


revision: str = "20260902_0005"
down_revision: Union[str, Sequence[str], None] = "20260902_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
