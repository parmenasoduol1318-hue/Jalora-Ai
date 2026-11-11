"""Alembic environment for Jalora AI.

URL comes from app settings (pydantic-settings, JALORA_* env / backend/.env)
so CLI (`alembic upgrade head` from backend/) and programmatic startup share
one source of truth. Offline mode uses the same URL.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app` importable when alembic runs standalone from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app import models  # noqa: F401,E402 - registers every model on metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Autogenerate only what the app's models own.

    `apscheduler_jobs` is created at runtime by APScheduler's SQLAlchemy job
    store, so it is not part of the model metadata and must never be dropped.
    """
    if type_ == "table" and name == "apscheduler_jobs":
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in --sql mode (no DB connection)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_as_batch=True,  # SQLite-compatible (ALTER TABLE emulation)
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live engine."""
    from app.database.session import engine

    connectable = engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
