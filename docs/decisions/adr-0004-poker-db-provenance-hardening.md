# ADR-0004 — DB provenance hardening: composite action provenance FK, UPDATE-path card-disjoint triggers, declaration-only pyproject.toml, and edit-in-place migration policy

**Status:** Accepted
**Task:** POKER-DB-HARDEN-001
**Date:** 2026-10-07
**Supersedes:** nothing; tightens the schema-level enforcement described in ADR-0002/ADR-0003.

## Context / problem

Independent review and test-audit of the POKER-DATA-SCHEMA-001 persistence
candidate found four gaps: (1) an action was only provenance-checked as
"seat exists" + "player exists", so an action at `(hand_id, seat_number)` could
attribute to a player seated at a *different* seat of the same hand;
(2) the per-hand card-disjointness triggers (ADR-0003) covered INSERT only —
a hole or board card could be *edited* into the other side of the same hand;
(3) the runtime/dependency contract (Python >= 3.12, SQLAlchemy 2.1.3,
Alembic 1.20.0 per ADR-0002) was not declared in any repository artifact;
(4) the batch-lineage foreign keys `import_batches.raw_source_id` and
`hands.import_batch_id` had no negative (fail-to-pass) tests.

## Decision

1. **Composite provenance FK** — `actions(hand_id, seat_number, player_id) ->
   hand_players(hand_id, seat_number, player_id)`, immediate and NOT
   deferrable, matching the existing `(hand_id, seat_number)` FK semantics.
   Both existing provenance constraints are retained: the `(hand_id,
   seat_number)` composite FK and the `player_id -> players.id` identity FK.
   The parent side gains a non-tightening
   `UNIQUE(hand_id, seat_number, player_id)` on `hand_players` because SQLite
   requires a UNIQUE index over the referenced columns.
2. **UPDATE-path closure** — two additional metadata-level triggers
   `trg_hole_card_update_not_on_board` and `trg_board_card_update_not_in_holes`
   (`BEFORE UPDATE OF card`, `WHEN EXISTS` against the opposite side of the
   same hand) with the same `IF NOT EXISTS` + `RAISE(ROLLBACK,
   'check failed: ...')` convention as ADR-0003. RAISE fires at statement
   time, so an illegal edit fails at execute.
3. **Runtime contract declared** — a root `pyproject.toml` with exactly a
   `[project]` table (no `[build-system]`, no tool configuration): the
   declaration pins the runtime boundary only; building/installing this
   repository as a package is explicitly out of scope.
4. **Edit-in-place for the initial migration** —
   `migrations/versions/a1c9f2b4e6d8_initial_schema.py` keeps delegating to
   `Base.metadata` (ADR-0003 decision 4); the new constraints/triggers flow
   into the DDL automatically and only the docstring mentions the change.

## Why this option

- Provenance is a relational fact about the triple (hand, seat, player), not
  about two separately-provable facts; decomposing it as "seat FK + player FK"
  is exactly the review finding reproduced by the new negative test.
- The 3-column parent UNIQUE is non-tightening: `UNIQUE(hand_id, seat_number)`
  already forces at most one row per seat, so the 3-column unique can never
  reject a row the 2-column ones allow; it exists solely to be a valid FK
  parent target.
- `player_id -> players.id` is kept as a separate identity FK: it fails with a
  precise "player does not exist" diagnosis for a case the 3-column FK would
  conflate with seat mismatches, and it is asserted by an existing oracle test.
- The UPDATE triggers reuse the INSERT-trigger convention (metadata attachment,
  `IF NOT EXISTS`, deterministic per-statement text) so
  `sqlite_master` determinism across two engines and the alembic-vs-create_all
  parity oracle stay structurally green.
- pyproject-as-declaration is the smallest truthful artifact: adding a build
  backend would imply packaging this effects-heavy harness repository, which
  no task authorizes.

## Alternatives considered

- **Deferrable provenance FK** — rejected: the single-transaction hand-write
  flow works with seat-existence deferred already; provenance correctness at
  statement time is stricter and matches the existing non-deferrable
  `(hand_id, seat_number)` FK semantics.
- **Dropping `player_id -> players.id` as redundant** — rejected (above).
- **Re-generating the initial migration with a new revision id** — rejected:
  the project is pre-release with no deployed databases, and the parity
  oracle compares against `Base.metadata`; a new revision id would break the
  deterministic-recreation contract for existing test/tooling flows for zero
  correctness gain.
- **Editing hole/board cards only through ORM-level validation** — rejected by
  the PHASE-A oracle principle: raw SQL must hit the same rules.

## Trade-offs and consequences

- Extra FK on `actions` adds a parent lookup per action insert — negligible at
  this scale (SQLite, local store).
- A new `hand_players` UNIQUE index adds minor write cost and changes stored
  DDL; schema state is reproducible deterministically (recreate from
  metadata), so no data migration is needed.
- `pyproject.toml` without a build backend emits packaging warnings if anyone
  attempts a build; accepted, declaration-only by design.
- Migration 1 is "edited history" for anyone who already ran `alembic upgrade
  head` before this change: their database now has fewer constraints than a
  fresh one; acceptable pre-release, and detectable by re-running the parity
  oracle, but recorded here as the known edit-in-place cost.

## Validation / evidence

- `tests/db/test_schema.py` grew from 67 to 72 tests, all green, including:
  `test_action_attributes_wrong_player_rejected` (RED → GREEN against the new
  composite FK), `test_hole_card_update_not_on_board_rejected` /
  `test_board_card_update_not_in_holes_rejected` (RED → GREEN against the new
  UPDATE triggers), and the batch-lineage negatives for
  `import_batches.raw_source_id` (INSERT form) and `hands.import_batch_id`
  (UPDATE form on a valid hand — an INSERT-only orphan-hands negative is
  confounded by the deferred composite `hands(id, button_seat)` FK, which
  rejects any hand without seated players at commit regardless of batch
  lineage; the used form has a mutually-exclusive mutation-check outcome).
- Both batch-lineage negatives were mutation-proven: with the declared FKs
  temporarily removed from `models.py` the tests fail; with them restored they
  pass.
- `TestSchemaRecreation`: two-engine deterministic recreation plus
  alembic-upgrade vs create_all parity oracle (PRAGMA table_info +
  PRAGMA foreign_key_list per table) all pass with the new constraints.
- Full suite: 658 prior tests plus 5 new, all green (`python -m unittest
  discover -s tests`).

## Explained simply

Actions must be signed not just by "a real player" and "a real seat" but by
the player actually sitting in that seat of that hand. Cards can no longer be
sneaked from a player's hand onto the board (or back) by editing them, only by
inserting. The Python/dependency floor is now written down in a standard file
that deliberately contains no build recipe, and the initial database recipe is
kept as the single source that both tool paths read, with only its
description updated.

## What I learned

- SQLite lets a composite FK target any UNIQUE index over exactly the
  referenced columns; a 3-column unique on the parent makes a 3-column FK
  enforceable without affecting which rows the existing 2-column uniques
  allow.
- A negative test that "always finds *some* IntegrityError" can lie about
  which constraint it proves — the deferred `hands(id, button_seat)` FK made
  an orphan-hands INSERT reject at commit even with the batch FK removed, so
  the mutation check had to be re-designed (UPDATE form) to stay
  discriminating.
- `RAISE(ROLLBACK, ...)` flames out the whole transaction at statement time;
  the UPDATE-path triggers inherit this and therefore satisfy raise-at-execute
  assertions.

## How I would explain it in an interview

I closed two provenance holes in a SQLite schema by extending the declaration
in exactly one place (`Base.metadata`) so both the deterministic-recreation
path and the Alembic path update together. The interesting part was test
design: negative tests must pin down *which* constraint fired, and one of them
was structurally confounded by a deferred FK, so I moved it to an UPDATE shape
that only fails under enforcement of the specific FK being audited.

## Revisit when

- Exactly-2-hole-cards-per-seat invariant is demanded at schema level
  (currently deferred; it is a per-seat cardinality rule, deferred as
  production-scope creep here).
- Trigger/index parity deltas surface between engines (PostgreSQL/duckdb) —
  revisit all four trigger statements and the FK-parent unique requirement.
- The repository ever becomes installable — then `[build-system]` and a real
  packaging decision are needed (human decision gate).

## Related artifacts

- `src/poker/db/models.py`, `tests/db/test_schema.py`,
  `migrations/versions/a1c9f2b4e6d8_initial_schema.py`
- `pyproject.toml` (declaration only)
- ADR-0002 (test-facing contract), ADR-0003 (session contract + trigger
  convention + metadata-delegating initial migration)
