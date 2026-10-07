"""Async database access with per-transaction RLS context (ADR-005, ADR-023).

Use :func:`transaction` for all DB work. When ``context`` is given, the RLS settings are
set with ``set_config(name, value, true)`` (the bind-parameter form of ``SET LOCAL``), so
they last only until the transaction ends. RLS policies return no rows without them.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Final

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

RLS_SETTINGS: Final = frozenset({"app.user_id", "app.grant_id"})

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION: Final = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all models (``app/<module>/models.py``)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class NestedTransactionError(RuntimeError):
    """Raised when ``transaction()`` is entered inside another ``transaction()``."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_in_transaction: ContextVar[bool] = ContextVar("in_transaction", default=False)


def get_engine() -> AsyncEngine:
    global _engine  # noqa: PLW0603 (lazy process-wide engine)
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url.get_secret_value(), pool_pre_ping=True
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker  # noqa: PLW0603
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def transaction(
    context: Mapping[str, str] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open a session and transaction; commit on success, roll back on error.

    Nesting is not supported: one request or job runs one transaction at a time, and an
    inner call would silently mix RLS contexts.
    """
    if _in_transaction.get():
        raise NestedTransactionError("transaction() cannot be nested")
    if context is not None:
        unknown = set(context) - RLS_SETTINGS
        if unknown:
            raise ValueError(f"unknown RLS settings: {sorted(unknown)}")

    token = _in_transaction.set(True)
    try:
        async with get_sessionmaker()() as session, session.begin():
            for name, value in (context or {}).items():
                await session.execute(
                    text("SELECT set_config(:name, :value, true)"),
                    {"name": name, "value": value},
                )
            yield session
    finally:
        _in_transaction.reset(token)
