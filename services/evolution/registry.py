"""PostgreSQL-backed immutable AgentBundle registry."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, TYPE_CHECKING

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .bundle import AgentBundle

if TYPE_CHECKING:
    from infrastructure.postgres import PostgresPool


class BundleConflictError(RuntimeError):
    """The same version names different content."""


class BundleNotFoundError(KeyError):
    """The requested bundle does not exist."""


class AgentBundleRegistry:
    """Immutable versions and an atomic bootstrap pointer, owned by PostgreSQL."""

    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def register(self, bundle: AgentBundle, *, actor: str = "system") -> AgentBundle:
        with self.pool.transaction() as connection, connection.cursor(row_factory=dict_row) as cursor:
            if bundle.base_version:
                base = cursor.execute(
                    "SELECT 1 FROM dialogpilot_platform.agent_bundles WHERE version=%s",
                    (bundle.base_version,),
                ).fetchone()
                if base is None:
                    raise BundleNotFoundError(bundle.base_version)
            cursor.execute(
                "INSERT INTO dialogpilot_platform.agent_bundles "
                "(version, base_version, content_hash, payload_json, created_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (version) DO NOTHING",
                (bundle.version, bundle.base_version, bundle.content_hash,
                 Jsonb(bundle.to_dict()), datetime.now(timezone.utc), str(actor)[:200]),
            )
            row = cursor.execute(
                "SELECT content_hash, payload_json FROM dialogpilot_platform.agent_bundles "
                "WHERE version=%s", (bundle.version,),
            ).fetchone()
            if row["content_hash"] != bundle.content_hash:
                raise BundleConflictError(
                    f"bundle version already has different content: {bundle.version}"
                )
            return AgentBundle(**row["payload_json"])

    def bootstrap(self, bundle: AgentBundle) -> AgentBundle:
        registered = self.register(bundle, actor="bootstrap")
        with self.pool.transaction() as connection:
            connection.execute(
                "INSERT INTO dialogpilot_platform.bundle_pointers "
                "(name, version, updated_at, updated_by) VALUES ('active', %s, %s, 'bootstrap') "
                "ON CONFLICT (name) DO NOTHING",
                (registered.version, datetime.now(timezone.utc)),
            )
        return registered

    def get(self, version: str) -> AgentBundle:
        with self.pool.transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM dialogpilot_platform.agent_bundles WHERE version=%s",
                (str(version),),
            ).fetchone()
        if row is None:
            raise BundleNotFoundError(str(version))
        return AgentBundle(**row[0])

    def active(self) -> AgentBundle:
        with self.pool.transaction() as connection:
            row = connection.execute(
                "SELECT b.payload_json FROM dialogpilot_platform.bundle_pointers p "
                "JOIN dialogpilot_platform.agent_bundles b ON b.version=p.version "
                "WHERE p.name='active'"
            ).fetchone()
        if row is None:
            raise BundleNotFoundError("active")
        return AgentBundle(**row[0])

    def list(self, limit: int = 100) -> List[dict]:
        with self.pool.transaction() as connection, connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(
                "SELECT b.version, b.base_version, b.content_hash, b.created_at, b.created_by, "
                "EXISTS (SELECT 1 FROM dialogpilot_platform.bundle_pointers p "
                "WHERE p.name='active' AND p.version=b.version) AS active "
                "FROM dialogpilot_platform.agent_bundles b "
                "ORDER BY b.created_at DESC, b.version DESC LIMIT %s",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [{**row, "created_at": row["created_at"].isoformat()} for row in rows]
