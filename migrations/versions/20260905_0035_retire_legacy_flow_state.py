"""Retire the unused pre-Target flow aggregate (destructive, forward-only).

Target control state is stored in conversation events, which are unchanged.
Archive any legacy flow rows before applying this migration outside test databases.
"""
from alembic import op

revision = "20260905_0035"
down_revision = "20260905_0034"
branch_labels = None
depends_on = None
subject_linked_write = False
data_location_ids = ()


def upgrade() -> None:
    op.execute("""
        DROP TRIGGER conversation_flow_state_delete
        ON dialogpilot_app.conversations
    """)
    op.execute("DROP TABLE dialogpilot_app.conversation_flow_state")
    op.execute("DROP FUNCTION dialogpilot_app.purge_flow_state_on_delete()")
    op.execute("DROP FUNCTION dialogpilot_app.guard_conversation_flow_state()")
    op.execute("""
        INSERT INTO dialogpilot_platform.data_location_registry_revisions (
            registry_version,artifact_fingerprint,artifact_path,approved_by
        ) VALUES (
            'v8',
            '26a3915a8a12b33c11d0e1fe97fad979765ed3c83ed6cdb7279c0bca059f8df9',
            'governance/data_locations/v8.json',
            'target-runtime-flow-state-retirement'
        )
    """)


def downgrade() -> None:
    raise RuntimeError("legacy flow-state retirement is forward-only")
