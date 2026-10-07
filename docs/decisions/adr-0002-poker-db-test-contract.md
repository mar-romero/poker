# ADR-0002 — Poker.db test-facing schema contract and Phase-A RED test surface

**Status:** Accepted (defines the Phase-B implementation contract for `POKER-DATA-SCHEMA-001`)
**Task:** POKER-DATA-SCHEMA-001 (R2, TDD two-phase RED)
**Date:** 2026-10-06

## Context / problem

The task requires an SQLAlchemy + Alembic schema for raw sources, imports,
players, aliases, hands, actions, boards, hole cards, uncalled returns and
decision points on local SQLite. Before implementation, an independent test
design was accepted that defines the behavior surface as a failing test suite
(Phase-A RED). Writing that suite forced several contract choices that the
implementation (Phase B) must satisfy; three were not fully pinned down by the
accepted design and had to be resolved against repository evidence:

1. Which public names must `poker.db` expose for the tests to drive it?
2. The accepted design's "10-seat full-table (25 distinct cards)" bullet
   conflicts with its own explicit seat bound "2..10 (1 and 11 → IntegrityError)":
   seats 2..10 allow at most 9 seated players, i.e. 9×2 hole + 5 board = 23
   distinct cards, never 25.
3. `hands.button_seat` is NOT NULL and must reference the seated players of the
   same hand via a composite foreign key `hands(id, button_seat) ->
   hand_players(hand_id, seat_number)` — a circular constraint, since
   `hand_players.hand_id` references `hands.id` and the full hand is written in
   a single transaction with `foreign_keys=ON`.

## Decision

1. **Public API contract (test-driven):** `poker.db`,
   `poker.db.models`, `poker.db.session` are importable;
   `poker.db.session.create_engine(db_path) -> sqlalchemy.Engine` returns a
   file-backed SQLite engine whose connections run with `PRAGMA foreign_keys=ON`,
   `PRAGMA journal_mode=WAL`, positive `PRAGMA busy_timeout`;
   `poker.db.models.Base.metadata.create_all(engine)` recreates the schema; and
   `migrations/` is a runnable Alembic script directory whose `env.py` reads the
   `sqlalchemy.url` main option so `alembic upgrade head` runs headless.
2. **Seat domain stays 2..10 (column CHECK)**, matching the canonical domain
   model `src/poker/domain/game.py` ("A physical table seat, numbered 2..10").
   The full-ring test therefore asserts the maximum legal configuration:
   9 seated players, 18 hole cards + 5 board cards = **23 distinct cards**, not
   the design brief's "25". This deviation is documented in the test module
   docstring and here rather than silently applied.
3. **Composite button FK is deferrable:** Phase B must declare
   `hands(id, button_seat) REFERENCES hand_players(hand_id, seat_number)` as
   `DEFERRABLE INITIALLY DEFERRED` so a hand row can be inserted with its button
   seat before its `hand_players` rows inside one transaction. The negative test
   helper accepts enforcement at execute time OR at commit time, so an immediate
   alternative is also detectable, but any correct implementation must fail
   commit when the button seat is not among that hand's players.

## Why this option

- The canonical domain model already fixes seats at 2..10; widening the seat
  domain in the schema to reach "25 cards" would create a second, conflicting
  seat convention. One authoritative seat domain is cheaper to keep correct.
- A single-transaction full-hand write is the AC the tests must accept
  (`test_full_hand_single_transaction_commits` inserts hand → hand_players →
  actions → cards in one transaction with `foreign_keys=ON`); SQLite checks
  deferred FKs at COMMIT, which is exactly the shape this write pattern needs.
- Lazy imports in the test surface guarantee that Phase-A RED is behavioral
  (every test individually reports `ModuleNotFoundError: No module named
  'poker.db'`) instead of a collection-time import failure that would mask
  per-test granularity.

## Alternatives considered

- **Seats 1..10** (to honor "25 distinct cards" literally): rejected —
  contradicts the explicit "1 → IntegrityError" bound in the accepted design and
  the canonical `Seat` domain in `src/poker/domain/game.py`; would fork the seat
  convention between domain and persistence layers.
- **Enforce "2..10 seated players per hand" via a trigger**: rejected for now —
  SQLite triggers cannot be deferred, so a `COUNT(*) < 2` trigger would wrongly
  reject the first `hand_players` insert of every hand; the accepted design
  bullet is parallel in construction to `street bounds 0..3` and refers to the
  `seat_number` column domain.
- **Non-composite button pointer (e.g., `hand_player_id` FK)**: rejected — the
  accepted design explicitly demands the composite FK to
  `hand_players(hand_id, seat_number)`, which also pins the button to the same
  hand.

## Trade-offs and consequences

- The tests define the public API before the code exists; Phase B must conform
  to these names or the suite fails with AttributeError instead of clean RED→GREEN.
- The per-table `PRAGMA table_info` + `PRAGMA foreign_key_list` equality oracle
  between `alembic upgrade head` and `create_all` is strict: migrations must
  produce byte-level-compatible column types, NOT NULL flags, defaults, PK
  positions and FK definitions. This is intentionally stronger than table-set
  equality but raises Phase-B effort.
- 23-vs-25 cards must be re-validated by the reviewer: the deviation is
  documented, not hidden.

## Validation / evidence

- Measured (this round): `python -m py_compile tests/db/test_schema.py` → exit 0;
  `$env:PYTHONPATH='src'; python -m unittest tests.db.test_schema` →
  `Ran 67 tests ... FAILED (errors=67)`, all 67 errors are
  `ModuleNotFoundError: No module named 'poker.db'`, exit code 1.
- Measured: resolved dependency versions `sqlalchemy==2.1.3`, `alembic==1.20.0`;
  Python 3.12, SQLite 3.49.1.
- Assumption (to be proven in Phase B): SQLite deferred FKs make the circular
  hand/button constraint satisfiable within one transaction.

## Explained simply

The tests are the blueprint: they describe exactly what the future database
layer must offer, and right now everything fails because the layer does not
exist yet. Three details needed judging: the exact public function names the
tests call; how many cards a full table can hold given this project counts
seats from 2 to 10 (so 23, not 25); and how to store the button seat so that a
whole hand can be written in one go without violating its own foreign keys
(SQLite's deferred checks solve this by checking at commit time).

## What I learned

- Before trusting a test-design summary, check it against the canonical domain
  code: the seat bound 2..10 resolved a numeric contradiction (25 vs 23 cards).
- SQLite's immediate vs deferred FK distinction is load-bearing for circular
  constraints inside one transaction.
- Uniform lazy-import RED makes every test an independent witness of the same
  missing module, which keeps the failure evidence comparable across tests.

## How I would explain it in an interview

I wrote the failing test suite first for a poker hand-history database schema.
The interesting part was resolving a contradiction in the accepted design
("10-seat, 25 cards" vs "seats 2..10") by deferring to the canonical domain
model and documenting the deviation instead of silently changing the seat
domain. The suite uses raw SQL for constraint negatives so ORM validators cannot
mask missing schema constraints, and a strict Alembic-vs-create_all parity
oracle. I'd change the seat-domain decision if the product later standardizes on
seat 1 existing, but then the domain model and schema must change together.

## Revisit when

- The product adopts a parser format that numbers seats 1..N (seat domain would
  widen; update the CHECK and the full-ring test together).
- The write path needs multi-transaction hand ingestion (deferred FK choice
  would need re-evaluation).
- Alembic autogenerate is introduced (per-column PRAGMA parity may need
  relaxing to a documented tolerance).

## Related artifacts

- `tests/db/__init__.py`, `tests/db/test_schema.py` (this round's outputs)
- `tasks/POKER-DATA-SCHEMA-001.json`
- `src/poker/domain/game.py` (canonical `Seat` domain 2..10)
- Supersedes nothing; related to `adr-0001-pots-contested-levels.md` (chip
  accounting domain feeding `uncalled_returns`).
