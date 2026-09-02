"""Install the retrieval write-approved data-location overlay.

Revision ID: 20260902_0009
Revises: 20260902_0008
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0009"
down_revision: Union[str, Sequence[str], None] = "20260902_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()

REGISTRY_VERSION = "v2"
REGISTRY_FINGERPRINT = "51e227f466f05185b20c8175b03dbfc852450b1644c371a23ccf5dcedbd5d745"


def upgrade() -> None:
    op.execute(f"""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version, artifact_fingerprint, artifact_path, approved_by
        ) VALUES (
            '{REGISTRY_VERSION}', '{REGISTRY_FINGERPRINT}',
            'governance/data_locations/v2.json',
            'privacy-platform-retrieval-review'
        )
    """)


def downgrade() -> None:
    raise RuntimeError("data-location registry downgrade requires forward-fix")
