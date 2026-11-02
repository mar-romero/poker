# Hand History Ingest Contract

Reference implementation: `src/poker/ingest/contracts.py` (`CONTRACT_VERSION = "1.0.0"`).
Tests: `tests/ingest/test_contracts.py`.

## Purpose

Venue-specific hand-history parsers stay isolated behind one provider-neutral
adapter contract so that a new room/site requires only a new adapter, never a
change to the domain model, the engine or the database layer. The contract
defines:

- the raw-input provenance a parse runs against (`RawSourceRef`),
- one normalized hand (`ParsedHand`) directly feedable to
  `StateEngine.replay(table, table.blinds, actions, button_seat=button_seat)`,
- the outcome of a parse call (`ParseResult`) with a pinned validity matrix,
- one diagnostic type (`ParseIssue`) discriminated into `warning`/`error`,
- the parser protocol every adapter must satisfy (`HandAdapter`).

The contract is structural, not strategic: it guarantees types, ordering and
bounds; legal-action checking remains the engine's job. Parsers may fail
partially and may never silently fabricate content (see
[Validity and fabrication rules](#validity-and-fabrication-rules)).

## Adapter protocol

`HandAdapter` is a `typing.Protocol` decorated with `@runtime_checkable`, so
any object with the right surface satisfies it without inheritance. Every
adapter exposes:

- `name: str` — the parser identity recorded in audit/log surfaces.
- `CONTRACT_VERSION: str` — MUST equal the
  `poker.ingest.contracts.CONTRACT_VERSION` the adapter was compiled against.
- `parse(raw_text: str, source_name: str) -> ParseResult` — pure function of
  the raw text and the source identifier; no global state, no network access.

The fabrication rule is binding on all implementers and is part of the
protocol documentation itself: `parse` NEVER synthesizes actions, cards, hands
or showdown outcomes that the raw text does not contain.

## ParseResult and ParsedHand

### ParseResult

`ParseResult(hands, warnings, errors, source)` is the outcome of one
`HandAdapter.parse` call. All four fields are frozen tuples/objects;
`warnings` and `errors` are separate lists and are NEVER merged into one
"issues" list, because a tolerable deviation (continue parsing) and untrusted
input (do not emit hands) have different consequences. The pinned validity
matrix:

| hands     | warnings | errors   | meaning                        | `ok`     |
| --------- | -------- | -------- | ------------------------------ | -------- |
| non-empty | empty    | empty    | clean success                  | `True`   |
| non-empty | non-empty| empty    | partial success (noted gaps)   | `True`   |
| empty     | empty    | empty    | no hands found                 | `True`   |
| empty     | any      | non-empty| invalid input                  | `False`  |
| non-empty | any      | non-empty| FORBIDDEN (raises `ValueError`) | —       |

`ParseResult.ok` is `True` exactly when `errors` is empty; a valid result may
still contain zero hands, so callers must check `hands` separately from `ok`.
Hand-level warnings live on each `ParsedHand.warnings` and are not duplicated
into the result-level `warnings` list.

### ParsedHand

`ParsedHand(table, actions, button_seat, hand_number, table_name,
street_reached, warnings=())` is one structurally valid normalized hand.
Invariants enforced at construction (fail-closed):

- `table` is a canonical `poker.domain.game.TableMetadata` (players, seats,
  blinds); seat count validated below.
- `button_seat` is a plain `int` in 2..10 and must be an occupied seat on the
  table (DB alignment: `hands.button_seat` is a deferred composite FK to
  `hand_players`).
- `hand_number` and `table_name` are non-empty strings (both are Text dedup
  key columns on `hands`).
- `street_reached` is a plain `int` in 0..3 (`PREFLOP`..`RIVER`).
- `actions` is a tuple of canonical `poker.domain.game.Action` instances with
  strictly increasing `sequence` values (no duplicates, no reordering — the
  engine replays in list order and never sorts).
- `warnings` contains only `'warning'`-kind `ParseIssue` instances.
- All instances are frozen dataclasses; mutation is rejected.

### Exact-key serialization

`ParsedHand.to_dict()` / `ParsedHand.from_dict()` follow the
`poker.domain.game` conventions:

- The payload embeds the domain integer `schema_version` (currently 1),
  validated fail-closed via `require_schema_version`.
- `require_exact_keys` rejects any extra or missing key at the top level and
  for every nested node (`blinds`, each player, each action, each warning).
- Payloads are plain dicts/lists of `str`/`int`/`None` — never floats and
  never sets — so `json.dumps(to_dict(), sort_keys=True)` is byte-stable
  across `to_dict` -> `from_dict` cycles.
- Two-layer dtype rule: chip amounts live as `Chips`/`BlindAnteConfig` in
  runtime domain objects, but serialized monetary values are plain
  non-negative ints matching the DB `Integer` columns (`small_blind`,
  `big_blind`, `ante`, `hand_players.stack`). Floats, bools, strings and
  `Decimal` are rejected at the boundary.

## Provenance and versioning

- `RawSourceRef(source_name, source_sha256)` carries the external source
  identifier and the SHA-256 hex digest (exactly 64 hex characters) of the raw
  content. Every normalized hand is traceable to its source bytes; the digest
  maps to `hands.content_sha256`.
- `CONTRACT_VERSION = "1.0.0"` is the parser/contract version. The ingest
  pipeline records it as `hands.parser_version` (a `Text` column, so a string
  semver is the right shape). Adapters must not drift from the contract
  version they were compiled against.
- Semver discipline:
  - MAJOR: shape or semantics break; previously parsed rows must not be mixed
    with new ones.
  - MINOR: additive, backward-compatible capability (e.g. the future
    tournament metadata slot below); previously parsed hands remain valid.
  - PATCH: fixes with no observable payload-shape change.
- A version bump is INTENTIONAL dedup divergence, not a bug: `hands` dedups
  on `(raw_source_id, table_name, hand_number, parser_version)` and audits
  re-parses on `(content_sha256, parser_version)`, so bumping
  `CONTRACT_VERSION` deliberately creates new rows for identical raw content
  instead of silently overwriting previously parsed hands.

## Validity and fabrication rules

- NEVER synthesize actions, cards, hands or showdown outcomes that the raw
  text does not contain. Unparseable or truncated input yields a
  `ParseResult` with `errors` populated and ZERO hands.
- Tolerable deviations (e.g. an unrecognized but harmless line) are recorded
  as `warnings`; gaps are surfaced, never filled silently.
- `warnings` and `errors` are separate lists everywhere and are never merged;
  `ParseIssue.kind` is restricted to exactly `'warning'` and `'error'`, and a
  `ParsedHand` rejects `'error'`-kind issues on its own `warnings` tuple.
- No silent normalization: if raw actions arrive reordered or duplicated, the
  parser must record the deviation (or fail with an error) — the contract
  re-sequences nothing on the caller's behalf.
- The contract contains no legal-action engine. Full legality is
  `StateEngine.replay`'s job; `ParsedHand` guarantees only the structural
  preconditions replay checks first (types, ordering, bounds).

## Seats, blinds and tournament extensibility

- Seat count: a `ParsedHand` table carries 2..10 seats (minimum heads-up,
  maximum full ring). Seat numbers and seat counts are two different
  2..10 ranges — see the deviation note in
  [Serialization and DB alignment](#serialization-and-db-alignment).
- Blinds: cash-game `sb`/`bb`/`ante` come from the canonical
  `BlindAnteConfig` on `TableMetadata` (validated non-negative there), so a
  `ParsedHand` needs no duplicated blind fields; serialized values are ints.
- Tournament metadata: RESERVED, NOT ADDED NOW. A future `Optional`
  tournament-metadata slot (e.g. buy-in, level, blind schedule) on the
  contract is the planned additive extension and would be a MINOR
  `CONTRACT_VERSION` bump with `None` as the default — additive, no change to
  the cash shape, previously parsed hands remain valid. Do not add placeholder
  tournament fields before a real tournament parser needs them.

## Serialization and DB alignment

Column map from the contract to the `hands` table
(`src/poker/db/models.py`):

| Contract field                      | `hands` column            | Notes |
| ----------------------------------- | ------------------------- | ----- |
| `hand_number`                       | `hand_number`             | Text, dedup key |
| `table_name`                        | `table_name`              | Text, dedup key |
| `CONTRACT_VERSION`                  | `parser_version`          | Text NOT NULL, dedup key |
| `RawSourceRef.source_sha256`        | `content_sha256`          | Text NOT NULL, re-parse audit key |
| `RawSourceRef.source_name`          | `raw_sources.name` via `raw_source_id` | FK provenance |
| serialized `blinds.sb` (int)        | `small_blind`             | Integer >= 0, never REAL |
| serialized `blinds.bb` (int)        | `big_blind`               | Integer >= 0 |
| serialized `blinds.ante` (int)      | `ante`                    | Integer >= 0 |
| `button_seat` (int 2..10)           | `button_seat`             | exactly one, deferred composite FK to `hand_players` |
| `street_reached` (int 0..3)         | `street_reached`          | 0..3 |

Explicit persistence boundary:

- **Warnings are contract-layer, in-memory only and are NOT persisted.** The
  `hands` table has no warnings column. Durable warning storage (import log /
  audit artifact) is a future ingest-pipeline concern, not a contract
  concern; this contract only guarantees warnings survive on the in-memory
  `ParseResult`/`ParsedHand` objects it returns.
- Raw player spellings are preserved by the parsers, not canonicalized here.
  Mapping parser-side spellings to canonical players via `PlayerAlias` is a
  downstream concern of the ingest pipeline.

Documented deviation — seat count vs seat number ("2..10" means two things,
both intentional):

- Canonical `Seat` NUMBERS are constrained to 2..10 (seat 1 is not allowed by
  the domain model), so the largest table constructible from canonical
  domain objects is physically 9 seats (seats 2..10).
- `ParsedHand` validates the seat COUNT as 2..10 (DB contract range), so a
  full-ring 9-seat table is the maximum currently expressible; a 10-seat
  count cannot be constructed until the canonical `Seat` numbering widens.
  Any future widening of seat numbering is a domain change, not an ingest
  contract change.
- Tests pin this explicitly (`test_seat_count_bounds_2_to_10_via_table_metadata`
  accepts `_table(9)` as the maximum) so the two "2..10" meanings are not
  accidentally conflated.
