# ADR-0003 — poker.db session contract, card-disjoint triggers, and metadata-delegating initial migration

**Status:** Accepted (Phase-B GREEN for POKER-DATA-SCHEMA-001)
**Task:** POKER-DATA-SCHEMA-001
**Date:** 2026-10-06
**Supersedes portions of:** ADR-0002 (implementation-level choices only; the test-facing contract stays as written there).

## Context / problem

Phase-A RED defined the oracle (`tests/db/test_schema.py`, 67 tests) but left
four implementation mechanisms open: (1) how the engine factory must behave so
raw-SQL fixtures see a usable schema without a separate init call; (2) how
SQLite transaction control must be configured on Python 3.12 so PEP-249
semantics, deferred foreign keys and the WAL pragma coexist; (3) where the
per-hand hole/board card-disjointness mechanism lives so ANY create_all path
gets it; (4) how the initial Alembic revision can guarantee EXACT
PRAGMA table_info / foreign_key_list parity with `Base.metadata.create_all`.

## Decision

1. **`poker.db.session.create_engine(db_path)` ensures the schema
   idempotently** — it registers its `connect` listener and then runs
   `Base.metadata.create_all(engine)` (checkfirst) with a lazy model import.
   Rationale: the oracle's fixtures write through factory connections without
   calling any init helper; create_all is idempotent and the `IF NOT EXISTS`
   trigger statements are no-ops on repeat (verified: SQLite stores trigger
   DDL normalized, without `IF NOT EXISTS`).
2. **Transactions: driver `sqlite3.connect(..., autocommit=False)`** (explicit
   PEP-249, per Python 3.12 guidance) + SQLAlchemy delegating semantics
   (`do_begin` is a no-op; commit/rollback delegate to the driver). The
   connect-time pragma block (`foreign_keys=ON`, `busy_timeout=10000`,
   `journal_mode=WAL`) temporarily flips the driver to `autocommit=True`
   because PEP-249 mode opens an implicit transaction before even a PRAGMA and
   SQLite forbids switching journal_mode inside a transaction (empirically
   reproduced), restoring `autocommit=False` afterwards.
3. **Per-hand card disjointness lives on `Base.metadata` via an
   `after_create` listener** emitting two `BEFORE INSERT` triggers (hole→board
   and board→hole, per hand) with `RAISE(ROLLBACK, 'check failed: ...')`.
   Attached to metadata so the test oracle (`Base.metadata.create_all`
   directly) and the migration both create them deterministically; `IF NOT
   EXISTS` keeps repeated create_all safe.
4. **The initial Alembic revision delegates to the shared metadata**
   (`Base.metadata.create_all(op.get_bind())` / `drop_all`). This makes the
   alembic-vs-create_all parity oracle structurally unable to drift. Future
   revisions MUST use explicit `op.*` steps against this frozen baseline.

## Why this option

- The oracle drives every fixture through the factory; a factory that does not
  ensure the schema turned 54 tests red with "no such table". Ensuring
  schema means "hand this module a path and you have a usable store" — the
  smallest contract that satisfies the accepted design.
- SQL CHECK constraints treat a NULL evaluation as a pass, so
  `post_type IN ('sb','bb','ante')` alone let a POST with NULL post_type
  through; the production fix adds an explicit `post_type IS NOT NULL` term
  (found by the oracle, fixed in production — tests not weakened).
- Delegating the initial revision to metadata is stronger than hand-written
  replication: hand-mirroring 11 tables byte-for-byte (types, defaults,
  deferrable FK, trigger DDL) is exactly what the strong parity oracle punishes.

## Alternatives considered

- **Separate `init_db`-only schema creation** (factory never creates
  tables): rejected — 54 oracle fixtures fail; the accepted design treats the
  factory as the complete entry point.
- **Native SQLite autocommit driver (`isolation_level=None`, SQLAlchemy-managed
  BEGIN)**: viable and the classic recipe, but the Phase-B contract specified
  explicit PEP-249 `autocommit=False`; deferred-FK-at-commit and rollback
  tests pass under it, so the stricter-to-negotiate option was kept and the
  deviation is only the temporary pragma-context flip, which is itself part of
  the cited SQLAlchemy recipe.
- **Frozen hand-written migration DDL**: rejected for the initial revision —
  high drift risk against an equality oracle; revisit when the schema evolves
  (see ADR-0002 "Revisit when").
- **Rejecting NULL-post_type at the ORM level only**: rejected — the oracle
  inserts with raw SQL; schema-level enforcement is mandatory.

## Trade-offs and consequences

- `create_engine` has a side effect (DDL on first use) — documented here and in
  the module docstring; acceptable for a local-first SQLite store, surprising
  for client/server engines (which are out of scope for this module).
- The deferred composite button FK remains sensitive to RAISE(ROLLBACK)
  trigger semantics: a card-disjoint trigger fire rolls back the whole
  transaction; the oracle's negative helper now tolerates that (test-side
  hygiene fix, documented below).
- Migration 1 depends on the models module for replay; installing migrations
  without `src/` on the path will fail at env.py (it inserts the path itself).

## Validation / evidence

- `python -m unittest tests.db.test_schema -v`: `Ran 67 tests ... OK`, exit 0
  (after cycles: run1 66 errors → pragma/wal fix; run2 54 errors →
  schema-ensuring factory; run3 3 defects → production CHECK NULL-in-term fix
  plus two test-hygiene fixes; run4 67 OK).
- Empirical probes: PEP-249 `autocommit=False` + `PRAGMA journal_mode=WAL`
  raises "cannot change into wal mode from within a transaction";
  sqlite_master stores normalized trigger DDL without `IF NOT EXISTS`.
- Checked defects and their classification: production defect 1
  (CHECK NULL semantics), test-hygiene defect 2 (`conn.rollback()` after
  RAISE(ROLLBACK) triggers), test-fixture defect 3 (hand-id lookup not unique
  across raw sources, now `ORDER BY id DESC LIMIT 1`).

## Explained simply

The module hands you a database path and returns a ready-to-use store: same
rules on every connection (foreign keys on, WAL journaling, retry timeout),
and the tables already exist. SQLite treats "no definite violation" in its
column rules as a pass, so one rule for blind posts had to say explicitly that
a missing blind post type is a violation. The tests learned to stay quiet when
SQLite already rolled a rejected write back by itself.

## What I learned

- SQL CHECK constraints FAIL only on FALSE; NULL evaluation silently passes —
  compound `IN` conditions must guard the NULL when rejection is intended.
- Python 3.12 `sqlite3` PEP-249 `autocommit=False` starts implicit
  transactions earlier than expected; pragma-time journaling needs an explicit
  autocommit window.
- `RAISE(ROLLBACK, ...)` in triggers kills the whole transaction at statement
  time; test helpers must tolerate an already-rolled-back connection.

## How I would explain it in an interview

I implemented the GREEN phase of a TDD'd SQLite schema: the failing suite became
67 passing tests. The interesting engineering was making one metadata the
authoritative source for both deterministic recreation and Alembic parity, and
getting Python 3.12 sqlite3 transaction semantics to coexist with WAL setup and
deferred foreign keys. The suite caught a real constraint bug (CHECK
NULL-passing) which I fixed in production, and two test-hygiene issues which I
documented as deviations rather than weakening assertions.

## Revisit when

- A second database engine (PostgreSQL/duckdb) is needed — revisit
  `RAISE(ROLLBACK)` triggers and PEP-249 driver assumptions.
- Schema revision 2 is authored — switch to explicit `op.*` and keep the
  parity oracle green.
- A hot ingest path needs lower write latency — revisit busy_timeout and WAL
  checkpointing configuration.

## Related artifacts

- `src/poker/db/session.py`, `src/poker/db/models.py`, `migrations/*`
- Oracle: `tests/db/test_schema.py`
- ADR-0002 (test-facing contract; unchanged), `planning/features/POKER-DATA-STORE-FEATURE-001.json`
