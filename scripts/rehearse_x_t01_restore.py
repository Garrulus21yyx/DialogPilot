"""Run an isolated head-schema dump/restore and governance-data reconciliation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

from infrastructure.postgres import PostgresMigrationRunner
from infrastructure.reconciliation import reconcile, snapshot_records


def _url(base: str, database: str) -> str:
    parsed = urlsplit(base)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _records(database_url: str) -> list[dict[str, str]]:
    with psycopg.connect(database_url) as connection:
        migrations = connection.execute(
            "SELECT revision, file_sha256 FROM dialogpilot_platform.migration_ledger ORDER BY revision"
        ).fetchall()
        registries = connection.execute(
            "SELECT registry_version, artifact_fingerprint FROM "
            "dialogpilot_platform.data_location_registry_revisions ORDER BY registry_version"
        ).fetchall()
    return [
        {"kind": "migration", "id": row[0], "hash": row[1]}
        for row in migrations
    ] + [
        {"kind": "data_location_registry", "id": row[0], "hash": row[1]}
        for row in registries
    ]


def rehearse(base_url: str, *, postgres_container: str = "") -> dict[str, object]:
    suffix = uuid.uuid4().hex[:10]
    source_name = f"dialogpilot_x01_source_{suffix}"
    restore_name = f"dialogpilot_x01_restore_{suffix}"
    parsed = urlsplit(base_url)
    admin_database = parsed.path.lstrip("/") or "postgres"
    admin_url = _url(base_url, admin_database)
    source_url, restore_url = _url(base_url, source_name), _url(base_url, restore_name)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="dialogpilot-x01-") as directory:
        dump_path = Path(directory) / "snapshot.dump"
        with psycopg.connect(admin_url, autocommit=True) as connection:
            for name in (source_name, restore_name):
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            source_verification = PostgresMigrationRunner(source_url).upgrade()
            source = snapshot_records(
                snapshot_id="x-t01-source", object_name="schema-governance",
                high_watermark=source_verification["head"], records=_records(source_url),
            )
            if shutil.which("pg_dump") and shutil.which("pg_restore"):
                subprocess.run(
                    ["pg_dump", "--format=custom", "--no-owner", "--file", str(dump_path), source_url],
                    check=True, capture_output=True,
                )
                subprocess.run(
                    ["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "--dbname", restore_url, str(dump_path)],
                    check=True, capture_output=True,
                )
            elif postgres_container:
                username = parsed.username or "postgres"
                dumped = subprocess.run(
                    ["docker", "exec", postgres_container, "pg_dump", "-U", username,
                     "--format=custom", "--no-owner", source_name],
                    check=True, capture_output=True,
                ).stdout
                dump_path.write_bytes(dumped)
                subprocess.run(
                    ["docker", "exec", "-i", postgres_container, "pg_restore",
                     "-U", username, "--exit-on-error", "--no-owner", "--no-privileges",
                     "--dbname", restore_name],
                    input=dumped, check=True, capture_output=True,
                )
            else:
                raise RuntimeError(
                    "pg_dump/pg_restore unavailable; provide --postgres-container"
                )
            restore_verification = PostgresMigrationRunner(restore_url).verify()
            restored = snapshot_records(
                snapshot_id="x-t01-restored", object_name="schema-governance",
                high_watermark=restore_verification["head"], records=_records(restore_url),
            )
            report = reconcile(source, restored)
            if not report.matched:
                raise RuntimeError("restored governance data does not match source snapshot")
            return {
                "schema_version": "dialogpilot-local-postgres-restore-v1",
                "environment": "local isolated PostgreSQL",
                "verification": "PASS",
                "alembic_head": source_verification["head"],
                "migration_ledger_sha256": source_verification["ledger_sha256"],
                "dump_sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
                "record_count": source.record_count,
                "records_sha256": source.sha256,
                "observed_rto_seconds": round(time.monotonic() - started, 3),
                "scope_limit": (
                    "Local empty-to-head schema snapshot only; no production snapshot "
                    "or production RTO claim."
                ),
            }
        finally:
            with psycopg.connect(admin_url, autocommit=True) as connection:
                for name in (source_name, restore_name):
                    connection.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=%s AND pid<>pg_backend_pid()", (name,),
                    )
                    connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--postgres-container", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = rehearse(
        args.database_url, postgres_container=args.postgres_container,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
