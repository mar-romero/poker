"""Alembic environment for the poker data-store schema migrations.

Headless-compatible: works when invoked with a programmatically built
``alembic.config.Config`` that only sets ``script_location`` and
``sqlalchemy.url`` (this is how the TDD oracle runs it, DJ: ADR-0002). The
``src`` directory of this repository is inserted into ``sys.path`` relative to
this file's location (repo root = parent of ``migrations/``), so the target
metadata is importable with or without ``PYTHONPATH=src``.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from alembic import context  # noqa: E402
from sqlalchemy import engine_from_config, pool  # noqa: E402

from poker.db.models import Base  # noqa: E402

# This is the Alembic Config object, which provides access to the values
# within the .ini file (or the programmatic Config) in use.
config = context.config

# Model's MetaData object for 'autogenerate' support; also the authoritative
# DDL source for the initial revision.
target_metadata = Base.metadata


def _database_url():
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "sqlalchemy.url is not configured: pass a main option "
            "'sqlalchemy.url' (e.g. 'sqlite:///<path>') before running "
            "alembic commands."
        )
    return url


def _prepend_sys_path():
    prepend = config.get_main_option("prepend_sys_path")
    if prepend:
        for entry in prepend.split(os.pathsep):
            if entry and entry not in sys.path:
                sys.path.insert(0, entry)


def run_migrations_offline():
    """Run migrations in 'offline' mode (emit SQL without a DBAPI)."""
    _prepend_sys_path()
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode against a live connection."""
    _prepend_sys_path()
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
