"""Engine and session factory for the poker data store (SQLite WAL).

Contract (tests/db/test_schema.py is the oracle; DJ: ADR-0002/ADR-0003):

- ``create_engine(db_path) -> sqlalchemy.Engine``: file-backed SQLite engine
  whose every connection runs with ``PRAGMA foreign_keys=ON``,
  ``PRAGMA journal_mode=WAL`` and a positive ``PRAGMA busy_timeout``.
- Pragmas are applied at the DBAPI driver level inside a ``connect`` event
  listener (per SQLAlchemy's SQLite dialect "connect event" recipe). Because
  PEP-249 ``autocommit=False`` opens an implicit transaction before even a
  PRAGMA (SQLite forbids changing journal_mode inside one), the listener
  temporarily switches the driver to autocommit for the pragma block and
  restores PEP-249 mode afterwards.

Transaction control: ``connect_args={'autocommit': False}`` selects PEP-249
explicit transaction semantics (Python 3.12 sqlite3 guidance, replacing the
legacy LEGACY_TRANSACTION_CONTROL default). This matches SQLAlchemy's pysqlite
dialect delegation (``do_begin`` is a no-op; commit/rollback delegate to the
driver), so BEGIN/rollback are correct and deferred foreign keys surface as
``IntegrityError`` at commit (DJ: ADR-0002).
"""

from __future__ import annotations

from sqlalchemy import create_engine as _create_sqlalchemy_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

BUSY_TIMEOUT_MS = 10000


def create_engine(db_path: str) -> Engine:
    """Create a WAL-configured, FK-enforcing SQLite engine at ``db_path``.

    The path is passed literally to sqlite3 (absolute or relative), one engine
    per database file. Handles receive identical pragma configuration on every
    (re)connect.
    """
    engine = _create_sqlalchemy_engine(
        "sqlite:///" + db_path.replace("\\", "/"),
        connect_args={"autocommit": False, "timeout": BUSY_TIMEOUT_MS / 1000.0},
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, connection_record):
        # PEP-249 autocommit=False opens an implicit transaction before even
        # a PRAGMA; journal_mode can only be set OUTSIDE a transaction. Flip
        # the driver to autocommit for the connect-time pragma block only
        # (SQLAlchemy SQLite dialect recipe) and restore PEP-249 mode after.
        dbapi_connection.autocommit = True
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")  # PS-2: provenance enforced
            cursor.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)  # PS-2
            cursor.execute("PRAGMA journal_mode=WAL")  # PS-2
        finally:
            cursor.close()
            dbapi_connection.autocommit = False

    # Ensure the schema exists idempotently (checkfirst), so plain fixture
    # writers and every handled connection see the 11 planned tables and the
    # card-disjoint triggers without a separate init step.
    from . import models  # lazy: keep module import free of heavy work

    models.Base.metadata.create_all(engine)
    return engine


def create_session(db_path: str) -> Session:
    """Convenience ORM session bound to a freshly configured engine."""
    engine = create_engine(db_path)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def init_db(db_path: str) -> Engine:
    """Create an engine and materialize the schema (deterministic recreation)."""
    engine = create_engine(db_path)
    from . import models  # lazy: keep module import free of heavy work

    models.Base.metadata.create_all(engine)
    return engine


def drop_db(db_path: str) -> Engine:
    """Create an engine and drop all tables (helper for test isolation)."""
    engine = create_engine(db_path)
    from . import models

    models.Base.metadata.drop_all(engine)
    return engine
