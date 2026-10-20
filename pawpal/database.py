"""Engine, session factory and schema initialisation.

SQLite notes
------------
* Foreign keys are switched on for every connection; file databases use WAL
  journaling so readers do not block the single writer.
* pysqlite's default "legacy" transaction handling runs DDL outside transactions
  and cannot take a write lock up front. We use SQLAlchemy's documented recipe
  instead: the driver is put in autocommit mode and SQLAlchemy emits ``BEGIN``
  itself. That makes migrations atomic and lets booking confirmation start with
  ``BEGIN IMMEDIATE`` (see ``write_locked_session``).
* The schema is created and upgraded by Alembic migrations (``pawpal/migrations``),
  never by ``create_all``. Existing Week 1 databases are upgraded in place.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from . import config

IMMEDIATE = "pawpal_sqlite_begin_immediate"


def make_engine(url: str | None = None) -> Engine:
    """Create an engine. SQLite connections always enforce foreign keys."""
    url = url or config.database_url()
    kwargs: dict = {}
    is_sqlite = url.startswith("sqlite")
    in_memory = url in ("sqlite://", "sqlite:///:memory:")
    if is_sqlite:
        # timeout = how long a writer waits for another writer's lock (seconds)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if in_memory:
            kwargs["poolclass"] = StaticPool  # one shared in-memory DB
    engine = create_engine(url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_on_connect(dbapi_connection, _record):
            dbapi_connection.isolation_level = None  # SQLAlchemy controls BEGIN
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if not in_memory:
                # WAL: readers never block the writer (several browser sessions at once).
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        @event.listens_for(engine, "begin")
        def _sqlite_on_begin(conn):
            if conn.get_execution_options().get(IMMEDIATE):
                conn.exec_driver_sql("BEGIN IMMEDIATE")  # take the write lock now
            else:
                conn.exec_driver_sql("BEGIN")

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine()


def init_db(engine: Engine | None = None) -> None:
    """Create or upgrade the schema to the latest migration (safe to call often)."""
    from .migrate import upgrade_db

    upgrade_db(engine or get_engine())


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Commit on success, roll back on error."""
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def write_locked_session(engine: Engine | None = None) -> Iterator[Session]:
    """Like ``session_scope`` but, on SQLite, starts with ``BEGIN IMMEDIATE``.

    The database write lock is held from the first statement, so two
    confirmations cannot both read "caregiver is free" and then both write.
    The second one waits (up to the connection timeout) and then sees the first
    one's committed visits. On other databases this is a normal transaction;
    see README for the PostgreSQL strategy.
    """
    engine = engine or get_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    if engine.dialect.name == "sqlite":
        session.connection(execution_options={IMMEDIATE: True})
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
