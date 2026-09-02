"""Shared external-service fixtures; PostgreSQL always uses an isolated database."""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_ticket_service import PostgresTicketService


def _database_urls(base_url: str, database_name: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    admin_url = urlunsplit((
        parsed.scheme, parsed.netloc, parsed.path or "/postgres",
        parsed.query, parsed.fragment,
    ))
    isolated_url = urlunsplit((
        parsed.scheme, parsed.netloc, f"/{database_name}",
        parsed.query, parsed.fragment,
    ))
    return admin_url, isolated_url


@pytest.fixture(scope="session")
def postgres_database_url():
    base_url = str(os.getenv("TEST_DATABASE_URL") or "").strip()
    container = None
    if not base_url and os.getenv("RUN_POSTGRES_TESTCONTAINER") == "1":
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("pgvector/pgvector:0.8.6-pg18-bookworm")
        container.start()
        base_url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://",
        )
    if not base_url:
        pytest.skip(
            "set TEST_DATABASE_URL or RUN_POSTGRES_TESTCONTAINER=1 for PostgreSQL tests"
        )

    parsed = urlsplit(base_url)
    database_name = f"dialogpilot_it_{uuid.uuid4().hex[:12]}"
    admin_url = urlunsplit((
        parsed.scheme, parsed.netloc, parsed.path or "/postgres",
        parsed.query, parsed.fragment,
    ))
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(
            sql.Identifier(database_name),
        ))
    isolated_url = urlunsplit((
        parsed.scheme, parsed.netloc, f"/{database_name}",
        parsed.query, parsed.fragment,
    ))
    try:
        yield isolated_url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(
                sql.Identifier(database_name),
            ))
        if container is not None:
            container.stop()


@pytest.fixture
def fresh_postgres_database_url():
    """Provide a per-test empty database for migration-path tests."""
    base_url = str(os.getenv("TEST_DATABASE_URL") or "").strip()
    if not base_url:
        pytest.skip("set TEST_DATABASE_URL for fresh PostgreSQL migration tests")
    database_name = f"dialogpilot_migration_{uuid.uuid4().hex[:12]}"
    admin_url, isolated_url = _database_urls(base_url, database_name)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(
            sql.Identifier(database_name),
        ))
    try:
        yield isolated_url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(
                sql.Identifier(database_name),
            ))


@pytest.fixture
def ticket_service(postgres_database_url):
    """Provide the sole Handoff owner with clean PostgreSQL facts per test."""
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url))
    pool.open()
    with pool.transaction() as connection:
        connection.execute(
            "TRUNCATE dialogpilot_app.handoff_ticket_outbox, "
            "dialogpilot_app.handoff_ticket_events, "
            "dialogpilot_app.handoff_tickets RESTART IDENTITY CASCADE"
        )
    service = PostgresTicketService(pool)
    try:
        yield service
    finally:
        pool.close()
