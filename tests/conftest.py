"""Shared external-service fixtures; PostgreSQL always uses an isolated database."""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql


@pytest.fixture(scope="session")
def postgres_database_url():
    base_url = str(os.getenv("TEST_DATABASE_URL") or "").strip()
    container = None
    if not base_url and os.getenv("RUN_POSTGRES_TESTCONTAINER") == "1":
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:18.1-alpine")
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
