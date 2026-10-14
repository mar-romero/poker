# ADR-0005: Hand state machine replay semantics (POKER-STATE-001)

- Date: 2026-10-14
- Status: Accepted
- Task: POKER-STATE-001 (risk R2, TDD, derived from POKER-INTELLIGENCE-001)
- Producers: implementer (writer), per planner handoff + test-designer handoff
  (B01-B09/C01-C22/A1-A9/U1-U6)

## Context

`poker.engine.state` replays normalized `Action` streams into immutable
`DecisionSnapshot`s at every reached decision point plus a terminal snapshot,
and returns a `StateResult` whose side-pot view is delegated to
`pots.build_pots`. `domain/game.py` (SCHEMA_VERSION 1) deliberately carries no
legal-action engine and no button seat; the engine must derive positions,
blind roles and bet sizing itself. Four ambiguities (explorer R1-R6, U1-U6)
required durable pins before the snapshot byte shape froze.

## Decisions

1. **Min-raise ladder with simplified all-in reopen semantics (A5 c/d).**
   (a) min bet = one big blind, or the remaining all-in stack when shorter;
   (b) min raise-to = current street top + max(max prior aggressive increment
   this street, big blind); (c) an all-in raise whose increment is below the
   required minimum is legal but does NOT reopen betting — players who
   already acted this street get no second decision point; (d) an all-in
   raise at-or-above the required minimum resets the acted set and reopens
   betting. DELIBERATE SIMPLIFICATION: the full tournament reopening
   exception (WSOP Rule-33 / TDA style, where callers below a sub-min all-in
   raise may still call the uncovered amount before showdown) is NOT
   implemented. Under this engine, prior callers keep their matched amount
   and the all-in excess flows out through the standard uncalled-return
   accounting in `pots.py` (`compute_uncalled_returns`), so chips are never
   lost and the final `StateResult` stays exactly consistent with
   `build_pots`. Cited canonical reference: WSOP 2023 Event Rules Rule 33
   ("sub-raise all-in does not reopen betting for players who have already
   acted, though they may call all or part of the raise"); TDA Rule 41.
   Recorded as a trade-off, not a silent defect (planner risk R4).

2. **Board cards are OUT of scope for snapshots (explorer R2/U6).**
   `DECISION_ENGINE_SPEC` lists board cards in the snapshot; the task JSON
   does not. Resolution: board replay belongs to ingest/decision-spot tasks
   that own card data. The frozen 12-key snapshot schema keeps a
   `schema_version`/`STATE_VERSION` bump as the extension path; stale
   consumers fail closed via `require_exact_keys` rejection.

3. **Heads-up postflop order: designer pin (documented convention).**
   Rotation is derived seat-wise: preflop first voluntary actor = strictly
   clockwise after the big blind; postflop rotation starts AT the small-blind
   seat (inclusive), which makes the button (=SB) act first postflop
   heads-up. This reproduces the test-designer/planner pin (C05/B02, planner
   risk R3), but diverges from standard poker seating rules, where the
   non-button player acts first postflop and the button acts last. The pin is
   a documented convention of this engine: multiway behavior is standard
   (SB, BB, ..., BTN); the heads-up postflop divergence must be revisited by
   the review layer or a spec change before ingest tasks depend on flop
   ordering derived from this module.

4. **POST-beyond-stack = all-in blind clamp (U3 reconciliation).**
   POST plays only in the preflop blind/ante batch. A blind/ante post that
   exceeds the poster's remaining stack is an all-in blind post: legal, and
   the contributed amount clamps at the remaining stack — but ONLY when the
   declared amount matches the role's blind value (or the poster's exact
   remaining stack, which must be below that value). Any other deviation from
   {sb, bb, ante>0} is an `InconsistentHistoryError`, never a silent
   contribution. Amount validation therefore happens after role inference
   (seat binding with poster-order tie-break for sb == bb; heads-up first
   poster is always BTN=SB). planner STEP 4's "InsufficientStackError
   otherwise" branch is superseded by this standard-ruleset pin.

5. **`button_seat` is a REQUIRED engine-level parameter.**
   `TableMetadata` (domain SCHEMA_VERSION 1) carries players/seats/blinds but
   no button seat, so `StateEngine.replay(..., *, button_seat: int)` takes it
   directly. Clockwise order = ascending seat number; `players` and `seats`
   pair positionally (index). Contract addition is recorded here and in the
   `state.py` module docstring; changing `domain.game.TableMetadata` to carry
   a button seat would require a domain schema bump and is out of scope.

6. **`action_index` snapshot semantics (U1).** 0-based index of the LAST
   APPLIED action; every snapshot precedes the NEXT action, so the provenance
   prefix is `actions[:snapshot.action_index + 1]`. Blind/ante POSTs are
   forced and emit no snapshot. Terminal hand end (last fold, or every
   remaining player all-in) emits one closing snapshot with
   `actor_to_act=None`, `legal_actions=()`,
   `is_betting_round_closed=True`.

## Evidence

- RED/GREEN TDD chain: `.harness/runs/POKER-STATE-001/tdd-evidence.jsonl`
  (design PASS, red exit 1 `899e73828abc…`, green exit 0 `40a604b60dc5…`,
  artifact `.harness/runs/POKER-STATE-001/green-output.txt`, 25 tests OK).
- Binding tests: `tests/engine/test_state_machine.py` (C11/C12/C12b exercise
  the ladder; C12b vs C12 contrast both reopen branches; C14 ante counts;
  C15-C17 blind binding; C19 byte stability; C21 seeds 1..20 cross-checks the
  final state against `build_pots`).
- Related: `docs/decisions/adr-0001-pots-contested-levels.md` (the pots
  pipeline this engine delegates side pots to).
