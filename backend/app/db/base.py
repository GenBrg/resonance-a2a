"""Database engine, session factory and schema bootstrap.

The schema is created from ORM metadata and stamped with a schema version. Any
future change ships as a numbered migration in ``app/db/migrations`` and is
applied by ``app.db.migrate``; the runner is intentionally tiny so there is no
framework to fight at 3am.
"""

from __future__ import annotations

import datetime as dt
import secrets
import string
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SCHEMA_VERSION = 1
_ALPHABET = string.ascii_lowercase + string.digits


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def new_id(prefix: str, length: int = 14) -> str:
    return f"{prefix}_{''.join(secrets.choice(_ALPHABET) for _ in range(length))}"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def as_aware(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite drops tzinfo; normalise on read so comparisons never explode."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        kwargs: dict = {"echo": settings.db_echo, "pool_pre_ping": True}
        if not settings.database_url.startswith("sqlite"):
            kwargs.update(pool_size=settings.db_pool_size, max_overflow=settings.db_pool_size)
        _engine = create_async_engine(settings.database_url, **kwargs)
        if settings.database_url.startswith("sqlite"):
            # Enforce FKs on *every* connection, not just the one that ran migrations.
            # Without this a deployment silently accepts orphan rows depending on
            # which connection the pool hands out — the worst kind of bug.
            from sqlalchemy import event

            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(engine(), expire_on_commit=False, autoflush=False)
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    session = session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_models() -> None:
    from app.db import models  # noqa: F401  (register mappers)

    if settings.database_url.startswith("sqlite"):
        target = settings.database_url.split("///", 1)[-1]
        if target and not target.startswith(":memory:"):
            from pathlib import Path

            Path(target).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    async with engine().begin() as conn:
        if settings.database_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
