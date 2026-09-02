"""SQLite 持久化的不可变 AgentBundle 注册表。"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List

from .bundle import AgentBundle


class BundleConflictError(RuntimeError):
    """相同版本名被用于不同内容。"""


class BundleNotFoundError(KeyError):
    """请求了不存在的 Bundle。"""


class AgentBundleRegistry:
    """Bundle 内容只追加；当前指针单独原子迁移。"""

    def __init__(self, db_path: str):
        self._path = str(pathlib.Path(db_path))
        pathlib.Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def register(self, bundle: AgentBundle, *, actor: str = "system") -> AgentBundle:
        payload = json.dumps(bundle.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT content_hash, payload_json FROM agent_bundles WHERE version=?",
                (bundle.version,),
            ).fetchone()
            if row is not None:
                if row["content_hash"] != bundle.content_hash:
                    raise BundleConflictError(f"bundle version already has different content: {bundle.version}")
                return self._decode(row["payload_json"])
            if bundle.base_version:
                base = conn.execute(
                    "SELECT 1 FROM agent_bundles WHERE version=?", (bundle.base_version,)
                ).fetchone()
                if base is None:
                    raise BundleNotFoundError(bundle.base_version)
            conn.execute(
                "INSERT INTO agent_bundles(version, base_version, content_hash, payload_json, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (bundle.version, bundle.base_version, bundle.content_hash, payload, now, str(actor)[:200]),
            )
        return bundle

    def bootstrap(self, bundle: AgentBundle) -> AgentBundle:
        """幂等注册首个 Bundle，并只在没有指针时设为 active。"""
        registered = self.register(bundle, actor="bootstrap")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO bundle_pointers(name, version, updated_at, updated_by) "
                "VALUES ('active', ?, ?, 'bootstrap')",
                (registered.version, datetime.now(timezone.utc).isoformat()),
            )
        return registered

    def get(self, version: str) -> AgentBundle:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM agent_bundles WHERE version=?", (str(version),)
            ).fetchone()
        if row is None:
            raise BundleNotFoundError(str(version))
        return self._decode(row["payload_json"])

    def active(self) -> AgentBundle:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT b.payload_json FROM bundle_pointers p JOIN agent_bundles b ON b.version=p.version "
                "WHERE p.name='active'"
            ).fetchone()
        if row is None:
            raise BundleNotFoundError("active")
        return self._decode(row["payload_json"])

    def list(self, limit: int = 100) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT version, base_version, content_hash, created_at, created_by "
                "FROM agent_bundles ORDER BY created_at DESC, version DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            active = conn.execute(
                "SELECT version FROM bundle_pointers WHERE name='active'"
            ).fetchone()
        active_version = active["version"] if active else ""
        return [{**dict(row), "active": row["version"] == active_version} for row in rows]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_bundles (
                    version TEXT PRIMARY KEY,
                    base_version TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bundle_pointers (
                    name TEXT PRIMARY KEY,
                    version TEXT NOT NULL REFERENCES agent_bundles(version),
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _decode(payload: str) -> AgentBundle:
        return AgentBundle(**json.loads(payload))
