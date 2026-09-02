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

from application.data_location_registry import (
    DataLocationRegistry,
    LocationReadiness,
    default_registry_path,
)
from core.schema_version_registry import SchemaVersionRegistry


class PostgresUnavailableError(RuntimeError):
    pass


class MigrationDriftError(RuntimeError):
    pass


class ForwardOnlyMigrationError(RuntimeError):
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
        return self.upgrade_to("head")

    def upgrade_to(self, target: str) -> dict[str, str]:
        try:
            target_revision = self._resolve_target(target)
            self._validate_data_location_metadata()
            self._validate_revision_chain()
            with self._migration_lock():
                self._verify_known_ledger(allow_absent=True)
                current = self._database_revision(allow_absent=True)
                ordered = self._ordered_revisions()
                positions = {
                    revision.revision: index
                    for index, revision in enumerate(ordered)
                }
                if current is not None and positions[current] > positions[target_revision]:
                    raise ForwardOnlyMigrationError(
                        f"downgrade is forbidden: {current} -> {target_revision}"
                    )
                command.upgrade(self.config, target_revision)
                self._record_applied_revisions(target_revision)
            return self.verify(target_revision)
        except (psycopg.Error, SQLAlchemyError, OSError) as exc:
            raise PostgresUnavailableError("PostgreSQL migration failed") from exc

    def verify(self, target: str = "head") -> dict[str, str]:
        self._validate_data_location_metadata()
        self._validate_revision_chain()
        target_revision = self._resolve_target(target)
        expected = self._revision_checksums(target_revision)
        try:
            with psycopg.connect(self.database_url) as connection:
                current = connection.execute(
                    "SELECT version_num FROM dialogpilot_platform.alembic_version"
                ).fetchone()
                rows = connection.execute(
                    "SELECT revision, file_sha256 FROM "
                    "dialogpilot_platform.migration_ledger"
                ).fetchall()
                registry_table = connection.execute(
                    "SELECT to_regclass("
                    "'dialogpilot_platform.data_location_registry_revisions')"
                ).fetchone()[0]
                registry_row = (
                    connection.execute(
                        "SELECT registry_version, artifact_fingerprint FROM "
                        "dialogpilot_platform.data_location_registry_revisions "
                        "ORDER BY registry_version DESC LIMIT 1"
                    ).fetchone()
                    if registry_table else None
                )
        except psycopg.Error as exc:
            raise PostgresUnavailableError("PostgreSQL verification failed") from exc
        actual = dict(rows)
        if actual != expected:
            raise MigrationDriftError(
                f"migration ledger mismatch: expected={sorted(expected)} "
                f"actual={sorted(actual)}"
            )
        if not current or current[0] != target_revision:
            raise MigrationDriftError(
                f"database revision {current[0] if current else None!r} "
                f"!= target {target_revision!r}"
            )
        if registry_row is not None:
            expected_registry = self._registry_at(target_revision)
            if expected_registry is None or registry_row != expected_registry:
                raise MigrationDriftError(
                    "installed data-location registry differs from migration chain"
                )
            if target_revision == self._script().get_current_head():
                registry = DataLocationRegistry.load(default_registry_path())
                if expected_registry != (registry.version, registry.fingerprint):
                    raise MigrationDriftError(
                        "migration head differs from approved data-location artifact"
                    )
        return {"head": target_revision, "ledger_sha256": _mapping_hash(actual)}

    def _validate_data_location_metadata(self) -> None:
        registry = DataLocationRegistry.load(default_registry_path())
        ordered = list(reversed(list(
            self._script().walk_revisions(base="base", head="heads")
        )))
        enforce = False
        for revision in ordered:
            if revision.revision == "20260902_0006":
                enforce = True
                continue
            if not enforce:
                continue
            module = revision.module
            if not hasattr(module, "data_location_ids") or not hasattr(
                module, "subject_linked_write",
            ):
                raise MigrationDriftError(
                    f"migration lacks data-location declaration: {revision.revision}"
                )
            if module.subject_linked_write and not module.data_location_ids:
                raise MigrationDriftError(
                    f"subject-linked migration has no locations: {revision.revision}"
                )
            for location_id in module.data_location_ids:
                location = registry.get(str(location_id))
                if location.readiness is not LocationReadiness.WRITE_APPROVED:
                    raise MigrationDriftError(
                        f"migration location is not write-approved: {location_id}"
                    )

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

    def _record_applied_revisions(self, target_revision: str) -> None:
        expected = self._revision_checksums(target_revision)
        with psycopg.connect(self.database_url) as connection:
            for revision, checksum in expected.items():
                connection.execute("""
                    INSERT INTO dialogpilot_platform.migration_ledger (
                        revision, file_sha256, applied_by, application_version
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (revision) DO NOTHING
                """, (revision, checksum, self.actor, self.application_version))
        self._verify_known_ledger(allow_absent=False)

    def _revision_checksums(self, target: str = "head") -> dict[str, str]:
        target_revision = self._resolve_target(target)
        return {
            revision.revision: _file_sha256(Path(revision.path))
            for revision in self._ordered_revisions()
            if self._revision_position(revision.revision) <= self._revision_position(target_revision)
        }

    def revision_manifest(self) -> tuple[dict[str, str | None], ...]:
        self._validate_revision_chain()
        return tuple({
            "revision": revision.revision,
            "down_revision": revision.down_revision,
            "file_sha256": _file_sha256(Path(revision.path)),
        } for revision in self._ordered_revisions())

    def _validate_revision_chain(self) -> None:
        script = self._script()
        heads = script.get_heads()
        if len(heads) != 1:
            raise MigrationDriftError(f"migration chain requires one head: {heads}")
        ordered = self._ordered_revisions()
        for index, revision in enumerate(ordered):
            expected = ordered[index - 1].revision if index else None
            if revision.down_revision != expected:
                raise MigrationDriftError(
                    f"migration chain is not linear at {revision.revision}"
                )
        if heads[0] != SchemaVersionRegistry.postgres.current_version:
            raise MigrationDriftError(
                "PostgreSQL head differs from schema-version registry"
            )

    def _ordered_revisions(self):
        return list(reversed(list(
            self._script().walk_revisions(base="base", head="heads")
        )))

    def _resolve_target(self, target: str) -> str:
        resolved = (
            self._script().get_current_head() if target == "head" else str(target)
        )
        if resolved not in {item.revision for item in self._ordered_revisions()}:
            raise MigrationDriftError(f"unknown migration target: {target}")
        return resolved

    def _revision_position(self, revision: str) -> int:
        return next(
            index for index, item in enumerate(self._ordered_revisions())
            if item.revision == revision
        )

    def _database_revision(self, *, allow_absent: bool) -> str | None:
        try:
            with psycopg.connect(self.database_url) as connection:
                exists = connection.execute(
                    "SELECT to_regclass('dialogpilot_platform.alembic_version')"
                ).fetchone()[0]
                if not exists and allow_absent:
                    return None
                row = connection.execute(
                    "SELECT version_num FROM dialogpilot_platform.alembic_version"
                ).fetchone()
                return str(row[0]) if row else None
        except psycopg.Error as exc:
            raise PostgresUnavailableError("PostgreSQL revision check failed") from exc

    @contextmanager
    def _migration_lock(self):
        """Serialize migration owners across processes without a schema dependency."""
        lock_key = int.from_bytes(
            hashlib.sha256(b"dialogpilot:postgres-migration:v1").digest()[:8],
            "big", signed=True,
        )
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            connection.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
            try:
                yield
            finally:
                connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))

    def _registry_at(self, target_revision: str) -> tuple[str, str] | None:
        result = None
        target_position = self._revision_position(target_revision)
        positions = {
            revision.revision: index
            for index, revision in enumerate(self._ordered_revisions())
        }
        for revision, version, fingerprint in (
            SchemaVersionRegistry.data_location_transitions
        ):
            if positions[revision] <= target_position:
                result = (version, fingerprint)
        return result

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
