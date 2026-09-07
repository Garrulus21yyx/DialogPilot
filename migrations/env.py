"""Online-only Alembic environment; DATABASE URL is supplied by the runner."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline() -> None:
    raise RuntimeError("offline SQL generation is not an approved migration path")


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS dialogpilot_platform")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=None,
            transaction_per_migration=True,
            version_table="alembic_version",
            version_table_schema="dialogpilot_platform",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
