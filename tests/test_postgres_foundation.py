"""M1-PF01 migration, pool and fail-closed integration contracts."""
import psycopg
import pytest

from infrastructure.postgres import (
    MigrationDriftError,
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
    PostgresUnavailableError,
)


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
    with pytest.raises(PostgresUnavailableError, match="ledger check failed"):
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
    assert first["head"] == "20260902_0015"

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
        assert ledger_count == 15
    finally:
        pool.close()


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
