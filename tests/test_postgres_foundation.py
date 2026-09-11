"""M1-PF01 migration, pool and fail-closed integration contracts."""
from concurrent.futures import ThreadPoolExecutor
import logging
from io import StringIO
from pathlib import Path
import subprocess
import sys

import psycopg
import pytest

from infrastructure.postgres import (
    ForwardOnlyMigrationError,
    MigrationDriftError,
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
    PostgresUnavailableError,
)


def test_migration_script_is_a_direct_cli_entrypoint():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/run_postgres_migrations.py", "--help"],
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--database-url" in result.stdout


def test_pool_config_requires_postgres_authority_and_valid_bounds():
    with pytest.raises(ValueError, match="SQLite fallback is forbidden"):
        PostgresPoolConfig.from_env({})
    with pytest.raises(ValueError, match="pool bounds"):
        PostgresPoolConfig("postgresql://example/test", min_size=2, max_size=1)


def test_unavailable_postgres_fails_closed_without_fallback():
    pool = PostgresPool(PostgresPoolConfig(
        "postgresql://invalid:invalid@127.0.0.1:1/missing?connect_timeout=1",
        min_size=0,
        max_size=1,
        timeout_seconds=1,
    ))
    with pytest.raises(PostgresUnavailableError, match="unavailable"):
        pool.open()


def test_unavailable_migration_is_typed_and_does_not_fallback():
    runner = PostgresMigrationRunner(
        "postgresql://invalid:invalid@127.0.0.1:1/missing?connect_timeout=1"
    )
    with pytest.raises(PostgresUnavailableError, match="migration failed"):
        runner.upgrade()


def test_empty_install_upgrade_pool_and_ledger_are_replayable(postgres_database_url):
    runner = PostgresMigrationRunner(
        postgres_database_url,
        actor="pytest",
        application_version="test",
    )
    first = runner.upgrade()
    second = runner.upgrade()
    assert first == second
    assert first["head"] == "20260911_0039"

    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    pool.open()
    try:
        with pool.transaction() as connection:
            isolation = connection.execute(
                "SHOW transaction_isolation"
            ).fetchone()[0]
            schemas = connection.execute(
                "SELECT current_schemas(false)"
            ).fetchone()[0]
            ledger_count = connection.execute(
                "SELECT count(*) FROM dialogpilot_platform.migration_ledger"
            ).fetchone()[0]
        assert isolation == "read committed"
        assert schemas[:2] == ["dialogpilot_app", "dialogpilot_platform"]
        assert ledger_count == 39
    finally:
        pool.close()


def test_migration_keeps_application_error_logging_enabled(postgres_database_url):
    log = logging.getLogger("application.migration_diagnostic_test")
    output = StringIO()
    handler = logging.StreamHandler(output)
    was_disabled = log.disabled
    log.disabled = False
    log.addHandler(handler)
    try:
        runner = PostgresMigrationRunner(postgres_database_url)
        for index in range(2):
            runner.upgrade()
            log.error("diagnostic-after-migration-%s", index)
        assert output.getvalue().splitlines() == [
            "diagnostic-after-migration-0", "diagnostic-after-migration-1",
        ]
    finally:
        log.removeHandler(handler)
        log.disabled = was_disabled
        handler.close()


def test_applied_migration_checksum_drift_fails_closed(postgres_database_url):
    runner = PostgresMigrationRunner(postgres_database_url)
    runner.upgrade()
    with psycopg.connect(postgres_database_url) as connection:
        original = connection.execute(
            "SELECT file_sha256 FROM dialogpilot_platform.migration_ledger "
            "WHERE revision='20260902_0001'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE dialogpilot_platform.migration_ledger SET file_sha256=%s "
            "WHERE revision='20260902_0001'",
            ("0" * 64,),
        )
    with pytest.raises(MigrationDriftError, match="ledger mismatch"):
        runner.verify()
    with psycopg.connect(postgres_database_url) as connection:
        connection.execute(
            "UPDATE dialogpilot_platform.migration_ledger SET file_sha256=%s "
            "WHERE revision='20260902_0001'",
            (original,),
        )


def test_progressive_and_skipped_forward_upgrades_share_one_linear_registry(
    fresh_postgres_database_url,
):
    runner = PostgresMigrationRunner(fresh_postgres_database_url)
    manifest = runner.revision_manifest()

    assert len(manifest) == 39
    assert manifest[0]["down_revision"] is None
    assert all(
        row["down_revision"] == manifest[index - 1]["revision"]
        for index, row in enumerate(manifest[1:], 1)
    )
    for row in manifest:
        revision = str(row["revision"])
        assert runner.upgrade_to(revision)["head"] == revision
    assert runner.upgrade()["head"] == "20260911_0039"


def test_execution_phase_migration_rejects_started_unfinished_legacy_runs(fresh_postgres_database_url):
    from tests.test_postgres_target_run import _admit_and_bind
    from infrastructure.postgres import MigrationRejected
    runner = PostgresMigrationRunner(fresh_postgres_database_url)
    runner.upgrade_to("20260911_0038")
    pool = PostgresPool(PostgresPoolConfig(fresh_postgres_database_url, min_size=1, max_size=2))
    pool.open()
    try:
        identity = _admit_and_bind(pool)
        with pool.transaction() as connection:
            connection.execute("UPDATE dialogpilot_app.compatibility_execution_outbox SET attempts=1 WHERE invocation_key=%s",
                               (str(identity.invocation_key),))
        with pytest.raises(MigrationRejected, match="Drain/reconcile"):
            runner.upgrade()
        with pool.transaction() as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260911_0038"
    finally:
        pool.close()


def test_migration_runner_rejects_downgrade_and_requires_forward_fix(
    postgres_database_url,
):
    runner = PostgresMigrationRunner(postgres_database_url)
    runner.upgrade()

    with pytest.raises(ForwardOnlyMigrationError, match="downgrade is forbidden"):
        runner.upgrade_to("20260902_0014")

    assert runner.verify()["head"] == "20260911_0039"


def test_concurrent_empty_database_migration_owners_serialize(
    fresh_postgres_database_url,
):
    def migrate(actor):
        return PostgresMigrationRunner(
            fresh_postgres_database_url, actor=actor,
        ).upgrade()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(migrate, ("runner-a", "runner-b")))

    assert {row["head"] for row in results} == {"20260911_0039"}
    assert len({row["ledger_sha256"] for row in results}) == 1
