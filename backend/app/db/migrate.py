"""Schema bootstrap and versioned migrations.

`create_all` handles a fresh install. Once a deployment exists, changes ship as
numbered SQL files in ``migrations/`` and this runner applies them in order inside a
transaction, recording each in ``schema_migrations``. Two functions, no framework.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text

from app.db.base import SCHEMA_VERSION, engine, init_models

logger = logging.getLogger("resonance.db")
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


async def migrate() -> dict:
    await init_models()
    applied: list[str] = []
    async with engine().begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        rows = (await conn.execute(text("SELECT version FROM schema_migrations"))).all()
        done = {int(row[0]) for row in rows}
        await conn.execute(
            text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
            {"v": SCHEMA_VERSION, "n": "baseline"},
        ) if SCHEMA_VERSION not in done else None
        if MIGRATIONS_DIR.exists():
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                try:
                    version = int(path.stem.split("_", 1)[0])
                except ValueError:
                    continue
                if version in done:
                    continue
                logger.info({"event": "migration_apply", "file": path.name})
                try:
                    await conn.exec_driver_sql(path.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    # A column added by `create_all` on a fresh install must not make
                    # the migration path fail on that same install.
                    if "duplicate column name" not in str(exc).lower():
                        raise
                    logger.info({"event": "migration_skipped_already_applied", "file": path.name})
                await conn.execute(
                    text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                    {"v": version, "n": path.name},
                )
                applied.append(path.name)
    return {"schema_version": SCHEMA_VERSION, "applied": applied}
