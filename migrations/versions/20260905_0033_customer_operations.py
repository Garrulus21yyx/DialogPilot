"""Move ecommerce business state to tenant-scoped PostgreSQL tables."""

from alembic import op

revision = "20260905_0033"
down_revision = "20260905_0032"
branch_labels = None
depends_on = None
# Schema-only: this migration does not write business subject rows.
subject_linked_write = False
data_location_ids = ()


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dialogpilot_app.customer_orders (
            tenant_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            refundable_until TEXT,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            shipping_address TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (tenant_id, order_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_orders_user_updated
            ON dialogpilot_app.customer_orders(tenant_id, user_id, updated_at DESC)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.customer_refund_requests (
            tenant_id TEXT NOT NULL,
            refund_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(tenant_id, order_id) REFERENCES dialogpilot_app.customer_orders(tenant_id, order_id),
            PRIMARY KEY (tenant_id, refund_id),
            UNIQUE (tenant_id, idempotency_key),
            UNIQUE (tenant_id, order_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_refunds_user_created
            ON dialogpilot_app.customer_refund_requests(tenant_id, user_id, created_at DESC)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.customer_order_cancellations (
            tenant_id TEXT NOT NULL,
            cancellation_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            order_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(tenant_id, order_id) REFERENCES dialogpilot_app.customer_orders(tenant_id, order_id),
            PRIMARY KEY (tenant_id, cancellation_id),
            UNIQUE (tenant_id, idempotency_key),
            UNIQUE (tenant_id, order_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_order_cancellations_user_created
            ON dialogpilot_app.customer_order_cancellations(tenant_id, user_id, created_at DESC)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.customer_shipping_address_changes (
            tenant_id TEXT NOT NULL,
            change_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            new_address TEXT NOT NULL,
            status TEXT NOT NULL,
            order_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(tenant_id, order_id) REFERENCES dialogpilot_app.customer_orders(tenant_id, order_id),
            PRIMARY KEY (tenant_id, change_id),
            UNIQUE (tenant_id, idempotency_key)
        )
    """)
    op.execute("""
        CREATE INDEX idx_address_changes_user_created
            ON dialogpilot_app.customer_shipping_address_changes(tenant_id, user_id, created_at DESC)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.customer_security_events (
            tenant_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            summary TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, event_id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_security_user_occurred
            ON dialogpilot_app.customer_security_events(tenant_id, user_id, occurred_at DESC)
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.customer_accounts (
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, user_id)
        )
    """)
    op.execute("""
        CREATE TABLE dialogpilot_app.customer_account_freezes (
            tenant_id TEXT NOT NULL,
            freeze_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            account_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(tenant_id, user_id) REFERENCES dialogpilot_app.customer_accounts(tenant_id, user_id),
            PRIMARY KEY (tenant_id, freeze_id),
            UNIQUE (tenant_id, idempotency_key)
        )
    """)
    op.execute("""
        CREATE INDEX idx_account_freezes_user_created
            ON dialogpilot_app.customer_account_freezes(tenant_id, user_id, created_at DESC)
    """)


def downgrade() -> None:
    raise RuntimeError("customer business state migrations are forward-only")
