"""Alembic environment (async). Runs as the ``app_migrator`` role (ADR-005)."""

import asyncio
import importlib
import importlib.util
import pkgutil
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app
from app.core.config import get_settings
from app.core.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _import_models() -> None:
    """Import every ``app/<module>/models.py`` so autogenerate sees all tables."""
    for info in pkgutil.iter_modules(app.__path__):
        name = f"app.{info.name}.models"
        if info.ispkg and importlib.util.find_spec(name) is not None:
            importlib.import_module(name)


_import_models()
target_metadata = Base.metadata
database_url = get_settings().migrator_database_url.get_secret_value()


# Monthly audit partitions are created at run time by audit_ensure_partitions (Story
# 7.1), not by migrations: autogenerate must never see them as tables to drop.
_RUNTIME_PARTITION = re.compile(r"audit_events_y[0-9]{4}m[0-9]{2}")


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    return not (type_ == "table" and name and _RUNTIME_PARTITION.fullmatch(name))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, include_name=include_name
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
