"""Move the immutable AgentBundle registry to the platform database."""
from alembic import op

revision = "20260905_0032"
down_revision = "20260903_0031"
branch_labels = None
depends_on = None
subject_linked_write = False
data_location_ids = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_platform.agent_bundles (
            version TEXT PRIMARY KEY,
            base_version TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            created_by TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_platform.bundle_pointers (
            name TEXT PRIMARY KEY,
            version TEXT NOT NULL REFERENCES dialogpilot_platform.agent_bundles(version),
            updated_at TIMESTAMPTZ NOT NULL,
            updated_by TEXT NOT NULL
        )
    """)


def downgrade() -> None:
    raise RuntimeError("AgentBundle registry migrations are forward-only")
