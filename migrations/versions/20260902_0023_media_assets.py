"""Add PostgreSQL-owned local media assets and turn bindings.

Revision ID: 20260902_0023
Revises: 20260902_0022
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0023"
down_revision: Union[str, Sequence[str], None] = "20260902_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
subject_linked_write = False
data_location_ids: tuple[str, ...] = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.media_assets (
            asset_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            modality TEXT NOT NULL CHECK(modality IN ('IMAGE','DOCUMENT')),
            media_type TEXT NOT NULL CHECK(media_type IN (
                'image/png','image/jpeg','application/pdf'
            )),
            byte_size BIGINT NOT NULL CHECK(byte_size > 0),
            checksum TEXT NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
            content BYTEA NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'UPLOADED','SCANNING','SCANNED','FAILED','QUARANTINED'
            )),
            schema_version TEXT NOT NULL CHECK(schema_version='media-asset-v1'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            UNIQUE (tenant_id,user_id,checksum),
            CHECK(octet_length(content)=byte_size)
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.media_asset_turn_bindings (
            asset_id TEXT NOT NULL REFERENCES dialogpilot_app.media_assets(asset_id)
                ON DELETE CASCADE,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            turn_key TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
            PRIMARY KEY (asset_id,turn_key)
        )
    """)
    op.execute("""
        CREATE INDEX media_asset_turn_bindings_subject_idx
        ON dialogpilot_app.media_asset_turn_bindings(tenant_id,user_id,turn_key)
    """)


def downgrade() -> None:
    raise RuntimeError("Media assets use forward-only migrations")
