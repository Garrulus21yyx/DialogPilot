"""Fail-closed PostgreSQL pool and migration owner."""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import Connection
from psycopg_pool import ConnectionPool
from sqlalchemy.exc import SQLAlchemyError


class PostgresUnavailableError(RuntimeError):
    pass


class MigrationDriftError(RuntimeError):
    pass


@dataclass(frozen=True)
class PostgresPoolConfig:
    database_url: str
    min_size: int = 1
    max_size: int = 10
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not str(self.database_url or "").strip():
            raise ValueError("database_url is required; SQLite fallback is forbidden")
        if self.min_size < 0 or self.max_size < 1 or self.min_size > self.max_size:
            raise ValueError("invalid PostgreSQL pool bounds")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "PostgresPoolConfig":
        values = env or os.environ
        return cls(
            database_url=str(values.get("DATABASE_URL") or ""),
            min_size=int(values.get("POSTGRES_POOL_MIN_SIZE", "1")),
            max_size=int(values.get("POSTGRES_POOL_MAX_SIZE", "10")),
            timeout_seconds=float(values.get("POSTGRES_POOL_TIMEOUT_SECONDS", "5")),
        )


class PostgresPool:
    def __init__(self, config: PostgresPoolConfig):
        self.config = config
        self._pool = ConnectionPool(
            conninfo=config.database_url,
            min_size=config.min_size,
            max_size=config.max_size,
            timeout=config.timeout_seconds,
            open=False,
            kwargs={"autocommit": False},
            configure=self._configure,
        )

    @staticmethod
    def _configure(connection: Connection) -> None:
        connection.execute(
            "SET search_path TO dialogpilot_app, dialogpilot_platform, public"
        )
        connection.commit()

    def open(self) -> None:
        try:
            self._pool.open(wait=True, timeout=self.config.timeout_seconds)
            with self._pool.connection() as connection:
                row = connection.execute("""
                    SELECT to_regnamespace('dialogpilot_platform') IS NOT NULL,
                           to_regnamespace('dialogpilot_app') IS NOT NULL
                """).fetchone()
                if row != (True, True):
                    raise PostgresUnavailableError(
                        "required schemas are absent; run migrations before startup"
                    )
        except PostgresUnavailableError:
            self._pool.close()
            raise
        except (psycopg.Error, TimeoutError) as exc:
            self._pool.close()
            raise PostgresUnavailableError("PostgreSQL is unavailable") from exc

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    yield connection
        except psycopg.OperationalError as exc:
            if exc.sqlstate is None or exc.sqlstate.startswith("08") or exc.sqlstate in {
                "57P01", "57P02", "57P03",
            }:
                raise PostgresUnavailableError("PostgreSQL transaction failed") from exc
            raise

    def close(self) -> None:
        self._pool.close()


class PostgresMigrationRunner:
    def __init__(
        self,
        database_url: str,
        *,
        config_path: str | Path = "alembic.ini",
        actor: str = "migration-runner",
        application_version: str = "unknown",
    ):
        if not str(database_url or "").strip():
            raise ValueError("database_url is required")
        self.database_url = database_url
        self.config_path = Path(config_path).resolve()
        self.actor = str(actor or "migration-runner")[:200]
        self.application_version = str(application_version or "unknown")[:200]
        self.config = Config(str(self.config_path))
        sqlalchemy_url = database_url
        if sqlalchemy_url.startswith("postgresql://"):
            sqlalchemy_url = sqlalchemy_url.replace(
                "postgresql://", "postgresql+psycopg://", 1,
            )
        self.config.set_main_option(
            "sqlalchemy.url", sqlalchemy_url.replace("%", "%%"),
        )

    def upgrade(self) -> dict[str, str]:
        try:
            self._verify_known_ledger(allow_absent=True)
            command.upgrade(self.config, "head")
            self._record_known_revisions()
            return self.verify()
        except (psycopg.Error, SQLAlchemyError, OSError) as exc:
            raise PostgresUnavailableError("PostgreSQL migration failed") from exc

    def verify(self) -> dict[str, str]:
        expected = self._revision_checksums()
        try:
            with psycopg.connect(self.database_url) as connection:
                current = connection.execute(
                    "SELECT version_num FROM dialogpilot_platform.alembic_version"
                ).fetchone()
                rows = connection.execute(
                    "SELECT revision, file_sha256 FROM "
                    "dialogpilot_platform.migration_ledger"
                ).fetchall()
        except psycopg.Error as exc:
            raise PostgresUnavailableError("PostgreSQL verification failed") from exc
        actual = dict(rows)
        if actual != expected:
            raise MigrationDriftError(
                f"migration ledger mismatch: expected={sorted(expected)} "
                f"actual={sorted(actual)}"
            )
        head = self._script().get_current_head()
        if not current or current[0] != head:
            raise MigrationDriftError(
                f"database revision {current[0] if current else None!r} != head {head!r}"
            )
        return {"head": head, "ledger_sha256": _mapping_hash(actual)}

    def _verify_known_ledger(self, *, allow_absent: bool) -> None:
        try:
            with psycopg.connect(self.database_url) as connection:
                exists = connection.execute(
                    "SELECT to_regclass('dialogpilot_platform.migration_ledger')"
                ).fetchone()[0]
                if not exists and allow_absent:
                    return
                rows = connection.execute(
                    "SELECT revision, file_sha256 FROM "
                    "dialogpilot_platform.migration_ledger"
                ).fetchall()
        except psycopg.Error as exc:
            raise PostgresUnavailableError("PostgreSQL ledger check failed") from exc
        expected = self._revision_checksums()
        for revision, checksum in rows:
            if expected.get(revision) != checksum:
                raise MigrationDriftError(f"applied migration changed: {revision}")

    def _record_known_revisions(self) -> None:
        expected = self._revision_checksums()
        with psycopg.connect(self.database_url) as connection:
            for revision, checksum in expected.items():
                connection.execute("""
                    INSERT INTO dialogpilot_platform.migration_ledger (
                        revision, file_sha256, applied_by, application_version
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (revision) DO NOTHING
                """, (revision, checksum, self.actor, self.application_version))
        self._verify_known_ledger(allow_absent=False)

    def _revision_checksums(self) -> dict[str, str]:
        revisions = list(self._script().walk_revisions(base="base", head="heads"))
        return {
            revision.revision: _file_sha256(Path(revision.path))
            for revision in reversed(revisions)
        }

    def _script(self) -> ScriptDirectory:
        return ScriptDirectory.from_config(self.config)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapping_hash(value: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for key, item in sorted(value.items()):
        digest.update(f"{key}\0{item}\n".encode())
    return digest.hexdigest()
