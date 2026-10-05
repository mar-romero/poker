# ADR-0001: Contested-level side-pot construction and pot accounting (POKER-CORE-POTS-001)

- Date: 2026-10-05
- Status: Accepted (one sub-assertion of the oracle recorded as BLOCKED; see below)
- Task: POKER-CORE-POTS-001 (risk R2, TDD)
- Producers: implementer (writer), oracle from test-designer handoff (PASS)

## Context

`build_pots(actions, stacks=None, rake_policy=None)` must turn normalized
actions into: per-player cumulative contributions, uncalled-bet returns,
side pots, effective stacks, and an optional rake hook. The test-designer
oracle pins goldens G1-G5, negatives N1-N9, properties P1-P3 (seeds 1..20).

## Decisions

1. **Contested-level rule (worded reading, contribution-only).**
   Contested levels are the sorted distinct positive contribution caps
   with >= 2 contributing players at-or-above the cap. `t_k` = highest
   contested cap (0 when none). `matched_p = min(total_p, t_k)`;
   `uncalled_p = total_p - matched_p` (post-hoc, exactly once per build,
   amounts >= Chips(1)). Side pots = one `domain.game.Pot` per contested
   level holding each player's delta slice
   `min(total_p, t_i) - t_{i-1}` (t_0 = 0), zero slices pruned, ascending
   thresholds, `rake=Chips(0)`. Conservation
   `Σcontributions == Σpot-contributions + Σuncalled` (pre-rake) holds by
   telescoping and is asserted for every golden and every property seed.
   - Rationale: this is the oracle's own pinned wording (B4/A5 + the
     implementation sketch: "contested levels = sorted distinct caps with
     >=2 contributors; above the max cap only 1 player => excess uncalled"),
     it reproduces G1/G3/G4 literally and every G5 chip quantity, and it is
     the standard poker side-pot ladder (each pot's contributors are exactly
     the players matched at-or-above that level).

2. **G5 literal pot list = BLOCKED oracle finding.**
   The test-designer's G5 expects a single merged pot
   `L100={UTG:100,BB:100,SB:50}` (total 250). Under the worded rule the same
   inputs produce the two-pot ladder `L50={UTG:50,BB:50,SB:50}` (150) and
   `L100={UTG:50,BB:50}` (100). The literal is internally inconsistent:
   - B3 ("each Pot's contributors are exactly the players whose matched
     contribution >= that level") fails for the literal: SB's matched is 50
     < 100, so SB can never be an L100 contributor.
   - G4 is structurally isomorphic to G5 (30/50/100 vs 50/100/300) and
     expects the SPLIT ladder, so no contribution-driven rule can satisfy
     both literals.
   - Real poker: the folded SB's 50 is dead money in the main pot; the
     50-100 side pot is separate. Both representations conserve chips and
     per-player matched totals ({UTG:100, BB:100, SB:50}) are identical, so
     no downstream equity/distribution semantics change.
   Action taken: implement the worded rule; keep every satisfiable G5
   expectation asserted (`test_g5_chip_identity`); mark the literal
   partition sub-assertion `@unittest.skip("BLOCKED: ...")` with the full
   analysis. The oracle was NOT silently weakened.

3. **Rake hook location.** Per A7 and the implementation prompt:
   `rake_policy` is invoked exactly once per build with the preliminary
   `PotBuildResult` (rake=Chips(0)); its return must be `isinstance Chips`
   else `TypeError`; the validated value is recorded on the RESULT
   (`PotBuildResult.rake`) via `dataclasses.replace`. Individual
   `Pot.rake` stays `Chips(0)`, so the per-pot identity
   `pot.total == Σcontributions + pot.rake` holds before and after the hook.

4. **Effective stacks.** `effective = initial_stack - cumulative normalized
   contribution` (raw contributions, pre-uncalled-refund, per B7/P3).
   Fail closed: `stacks=None` with any nonzero contribution ->
   `InsufficientContextError`; contributing player absent from `stacks` ->
   `InsufficientContextError`; contribution > stack -> `ValueError`
   (impossible history); contribution == stack is legal (effective 0).

5. **Fold semantics.** Folds are normalization no-ops that contribute
   nothing (B6). Contested levels are computed from final contributions
   only; fold status does not establish or remove pot levels (dead chips
   are included in the slices they can reach, which is what makes G1-G4
   and conservation hold). Street-level matched-sequence modeling is out of
   scope for this module (oracle A5/A6 model).

## Consequences

- All four acceptance criteria are covered by named tests: multiway unequal
  all-ins (G1/G3), exactly-once uncalled returns (G2/N2 + O5 identities),
  pre-rake chip conservation (O2 universal), property coverage (P1-P3,
  seeds 1..20).
- Residual: the G5 oracle slip (decision 2) needs test-designer
  confirmation; downstream consumers must read rake from
  `PotBuildResult.rake`, not from individual pots.
