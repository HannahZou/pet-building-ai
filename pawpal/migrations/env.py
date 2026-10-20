"""Alembic environment. Used by `pawpal.migrate.upgrade_db` and the `alembic` CLI."""
from __future__ import annotations

from alembic import context

from pawpal import config as app_config
from pawpal.database import make_engine
from pawpal.models import Base

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite-friendly ALTERs for future autogenerate
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:  # called from pawpal.migrate with an open transaction
        _run(connection)
        return
    url = context.config.get_main_option("sqlalchemy.url") or app_config.database_url()
    engine = make_engine(url)
    with engine.begin() as conn:
        _run(conn)


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations are not supported; run against a database.")
run_migrations_online()
