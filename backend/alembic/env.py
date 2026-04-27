from __future__ import annotations

from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from app import models  # noqa: F401
from app.db import _build_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

database_url = os.getenv("DATABASE_URL") or _build_database_url()
config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    import logging, traceback as _tb
    _diag = logging.getLogger("alembic.diag")

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # DDL tracing: log any CREATE TABLE for tables that shouldn't exist yet
    from sqlalchemy import event as _sa_event
    @_sa_event.listens_for(connectable, "before_cursor_execute")
    def _trace_ddl(conn, cursor, statement, parameters, context_, executemany):
        if "budgetpolicy" in statement.lower():
            _diag.warning("TRACE budgetpolicy DDL:\n%s\nSTACK:\n%s", statement[:300], "".join(_tb.format_stack()))

    with connectable.connect() as connection:
        # Acquire a PostgreSQL advisory lock so concurrent migration processes
        # (e.g., multiple pods starting up) don't race each other.
        if connectable.dialect.name == "postgresql":
            from sqlalchemy import text as _text
            connection.execute(_text("SELECT pg_advisory_lock(20260301)"))

        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()

        # pg_advisory_lock() starts an implicit transaction; alembic's
        # begin_transaction() becomes a savepoint inside it, so we must
        # commit the outer connection explicitly to persist DDL.
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
