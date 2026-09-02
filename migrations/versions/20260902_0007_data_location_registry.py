"""Bind the approved data-location registry artifact.

Revision ID: 20260902_0007
Revises: 20260902_0006
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0007"
down_revision: Union[str, Sequence[str], None] = "20260902_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every migration after 0006 must declare the registered subject-linked locations
# it creates or mutates. This migration only installs the non-subject registry binding.
data_location_ids: tuple[str, ...] = ()
subject_linked_write = False

REGISTRY_VERSION = "v1"
REGISTRY_FINGERPRINT = "92760d381381231733d03ac8f11b6cd4d8aa75931c68988aa01719545c40fd28"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_platform.data_location_registry_revisions (
            registry_version TEXT PRIMARY KEY,
            artifact_fingerprint TEXT NOT NULL UNIQUE
                CHECK(length(artifact_fingerprint) = 64),
            artifact_path TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            installed_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
        )
    """)
    op.execute(f"""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version, artifact_fingerprint, artifact_path, approved_by
        ) VALUES (
            '{REGISTRY_VERSION}', '{REGISTRY_FINGERPRINT}',
            'governance/data_locations/v1.json', 'privacy-platform-review'
        )
    """)
    op.execute("""
        CREATE TRIGGER data_location_registry_revisions_immutable
        BEFORE UPDATE ON dialogpilot_platform.data_location_registry_revisions
        FOR EACH ROW EXECUTE FUNCTION dialogpilot_platform.reject_immutable_update()
    """)


def downgrade() -> None:
    raise RuntimeError("data-location registry downgrade requires forward-fix")
