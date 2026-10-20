"""Run Alembic migrations programmatically.

* Fresh database      -> upgrade from nothing to head.
* Week 1 database     -> it has tables but no ``alembic_version``: stamp it as the
                         Week 1 baseline (no tables touched), then upgrade.
* Up-to-date database -> no-op.

Before upgrading an existing SQLite *file*, a timestamped backup copy is written
next to it (``pawpal.db.bak-YYYYmmdd-HHMMSS``). Run: ``python -m pawpal.migrate``.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

logger = logging.getLogger(__name__)

BASELINE = "0001_week1"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "pawpal:migrations")
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _backup_sqlite_file(engine: Engine) -> Path | None:
    db = engine.url.database
    if engine.dialect.name != "sqlite" or not db or db == ":memory:":
        return None
    path = Path(db)
    if not path.exists() or path.stat().st_size == 0:
        return None
    backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    # SQLite's online backup API also captures commits still in the WAL file.
    src, dst = sqlite3.connect(path), sqlite3.connect(backup)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    logger.info("Backed up %s to %s before migrating", path, backup)
    return backup


def upgrade_db(engine: Engine) -> None:
    head = head_revision()
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        current = MigrationContext.configure(conn).get_current_revision()
    if current == head:
        return
    if tables:
        _backup_sqlite_file(engine)

    cfg = alembic_config()
    with engine.begin() as conn:  # one transaction: all migrations or none
        cfg.attributes["connection"] = conn
        if current is None and "bookings" in tables:
            command.stamp(cfg, BASELINE)  # existing Week 1 database
        command.upgrade(cfg, "head")


def main() -> None:
    from .database import get_engine

    engine = get_engine()
    before = current_revision(engine)
    upgrade_db(engine)
    print(f"Database migrated: {before or 'empty/Week 1'} -> {current_revision(engine)}")


if __name__ == "__main__":
    main()
