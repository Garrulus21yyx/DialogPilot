"""Register the disposable, deletion-fenced conversation snapshot cache."""
from alembic import op

revision = "20260911_0038"
down_revision = "20260908_0037"
branch_labels = None
depends_on = None
subject_linked_write = False
data_location_ids = ()


def upgrade() -> None:
    op.execute("""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version,artifact_fingerprint,artifact_path,approved_by
        ) VALUES (
            'v9',
            'ec82f06b019c4ae5cd29f0c5cf8c4acc1f1218af61c6bc898f03e63f3f15a075',
            'governance/data_locations/v9.json',
            'conversation-cache-consistency-review'
        )
    """)


def downgrade() -> None:
    raise RuntimeError("data-location registry is forward-only")
