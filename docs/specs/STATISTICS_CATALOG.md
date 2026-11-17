# Statistics Catalog and Query Model

## Design rule
A statistic is not just a percentage. Canonical definition:

`Stat = Measure(OpportunityPredicate, SuccessPredicate, Dimensions, DefinitionVersion)`

Every result exposes at least: successes/numerator, opportunities/denominator, raw rate, definition version, filters, sample window, and optional posterior estimate/credible interval. Contextual variants are produced by dimensions instead of creating a bespoke code path for every combination.

## Core dimensions
Player/hero position; opponent position; heads-up/multiway; players dealt/players to flop; effective-stack bucket and raw stack; table size; blind/ante structure; pot type (limped/SRP/3BP/4BP/5BP); initiative; facing action and prior action sequence; IP/OOP; street; board texture; turn/river transition; bet/raise sizing bucket and raw fraction; number of bets/raises; all-in state; showdown state; session/recent/lifetime window; player archetype/cohort; rake/stake configuration.

## Preflop base measures
- Hands / opportunities / voluntarily-put-money-in-pot (VPIP)
- PFR and open-raise (RFI) by position
- limp, open-limp, over-limp, limp/fold, limp/call, limp/raise
- iso-raise and response to isolation
- cold-call and cold 4-bet
- 3-bet, 3-bet vs open, 3-bet vs position, 3-bet IP/OOP
- fold/call/4-bet vs 3-bet
- 4-bet, 4-bet range/frequency, fold/call/5-bet vs 4-bet
- 5-bet/jam where applicable
- squeeze, fold/call/back-raise vs squeeze
- steal attempt by CO/BTN/SB and composite steal
- SB/BB fold/call/3-bet vs steal by opener position
- BB defense vs each position; defend by call vs 3-bet
- SB complete/raise/fold vs unopened pot; SB-vs-BB dynamics
- blind-vs-blind limp/stab/raise trees
- shove/open-jam and response by effective stack
- raise size distributions and position-conditioned sizing

### Implemented preflop catalog (task POKER-STATS-PREFLOP-001; additive)

Implemented in `src/poker/stats/catalog/preflop.py`, evaluated purely over
`StateEngine.replay` artifacts (two-replay composition with the frozen
`poker.stats.evaluate_hand`; no IO, deterministic). The 17 new definitions
live under the independent constant `PREFLOP_DEFINITIONS_VERSION = '1.0.0'`
(parallel to — never merged with — the frozen six-definition registry in
`poker.stats.definitions`, `DEFINITIONS_VERSION = '1.0.0'`; the pinned six
are reused by identity, not re-declared). Combined registry: 23 preflop stat
ids in fixed order.

Implemented stat IDs (opportunity predicate → success):

- `vpip`: actor's FIRST non-excluded preflop decision → CALL or RAISE (a BB
  check is never a success; re-opened second decisions are never
  opportunities).
- `pfr`: same opportunity → RAISE. PIN: limp-then-raise is NOT a PFR success
  (the raise occurs at a re-opened decision, excluded by the gate).
- `rfi`, `open_limp`: unopened (rc == 0, cc == 0) → RAISE / CALL.
- `sb_complete`: unopened, position SB → CALL (intentionally also matches
  `open_limp` at the same decision point — one decision point may carry
  matches for several stats).
- `over_limp`, `iso_raise`: limped (rc == 0, cc >= 1) → CALL / RAISE.
- `fold_to_iso`, `call_to_iso`: rc == 1, cc >= 1 → FOLD / CALL;
  `opener_position` = iso-raiser (last preflop raiser).
- `cold_call`: rc == 1, cc == 0 → CALL; `opener_position` = lone opener.
- `four_bet`: rc == 2 (regardless of cc) → RAISE.
- `five_bet`, `fold_to_four_bet`, `call_to_four_bet`: rc == 3 → RAISE /
  FOLD / CALL; `opener_position` = last preflop raiser (the 4-bettor).
- `squeeze`: rc == 1, cc >= 1 → RAISE.
- `fold_to_squeeze`, `call_to_squeeze`: rc == 2, cc >= 1 → FOLD / CALL;
  `opener_position` = squizzer (last preflop raiser).

Every result is exposed as a frozen `StatRate` with `successes`,
`opportunities`, `raw_rate` (None on zero opportunities — never shown as
0.0) and `definition_version`. Every definition is machine-readable via
`preflop_definitions_to_dict()` (`predicate_spec` / `success_spec` /
`dimensions` / `exclusions`) under a CLOSED predicate vocabulary.

Documented pinned divergences (tracker-convention, recorded not silent):

- `fold_to_three_bet` keeps its FROZEN semantics from
  `poker.stats.definitions`: a FOLD facing a LONE open (rc == 1, cc == 0);
  re-opened decisions (rc >= 2) match nothing there.
- `pfr` first-decision pin (above): limp-then-raise is never a PFR success.
- POSITIONS is the lossy analytics vocabulary of
  `poker.features.schema.build_positions`: interior seats beyond UTG/CO
  collapse UTG+1..HJ into `MP`; no IP/OOP dimension is derived.
- FACING_ACTIONS `facing-4bet+` is an AGGREGATION LABEL only: the predicate
  vocabulary keys on exact same-street raise counts, so a fold facing a
  5-bet (rc == 4) matches no response statistic.
- Composite steal (CO/BTN/SB attempt) is the AGGREGATION of the single
  pinned `steal` definition over its `position` dimension — not a separate
  code path.

DEFERRED (explicitly out of scope for this task, with rationale):

- All-in/open-jam measures and effective-stack-conditioned responses: the
  D3 exclusion rule keeps facing-all-in decisions as generic opportunities,
  but no all-in-aware predicates (jam/shove-over/facing-shove-by-stack) are
  defined yet; adding them requires a new definition version.
- Raise-size distributions and quantiles: sizing is out of the frequency
  evaluator's scope (see "Sizing distributions"); needs raw pot-before
  conventions and bucket governance.
- IP/OOP dimension: POSITIONS deliberately carries no in-position flag;
  would require a definition-version bump and a new dimension.
- Posterior/Bayes estimates and credible intervals: display layer over
  aggregated counts (see "Sample and uncertainty display"), not a
  per-hand evaluation concern.
- DSL aggregation: generated-stat composition (e.g. "BB fold vs BTN open,
  80-120bb") is downstream of these definitions and belongs to the DSL
  task.

## Flop base measures
- c-bet, c-bet IP/OOP, in SRP/3BP/4BP and multiway
- c-bet by sizing (<=25%, 25-40%, 40-60%, 60-90%, pot, overbet; retain raw size)
- fold/call/raise vs c-bet by size and board class
- check-raise opportunity/frequency and response to check-raise
- donk bet, donk/fold, donk/call, donk/3bet
- probe/stab after missed c-bet where definitions apply
- float / call-flop-then-bet-turn after check
- flop bet when checked to, check-back, fold-to-stab
- continuation after preflop 3bettor/caller roles
- aggression frequency and factor components

## Turn base measures
- turn barrel after flop c-bet; give-up/check
- fold/call/raise vs turn barrel
- delayed c-bet after flop check-back
- probe turn after flop checked through / missed c-bet
- second barrel by turn-card class (blank, overcard, pair, flush/straight completing, etc.)
- turn check-raise / donk / block sizing / overbet
- flop-call-turn-fold and flop-call-turn-continue line metrics

## River base measures
- triple barrel; river give-up; river bet after prior checks
- fold/call/raise vs river bet by size
- river overbet, pot bet, block bet, check-raise, donk
- bet-to-showdown value/bluff observed classifications when cards are revealed (do not infer hidden bluff truth as fact)
- bluff-catch call outcomes with explicit revealed-card subset caveat
- missed-draw bluff candidates only as modeled/inferred metrics, never observed truth without showdown

## Showdown / outcome measures
- WTSD (went to showdown) with exact saw-flop eligibility definition
- W$SD (won money at showdown)
- WWSF (won when saw flop)
- showdown/non-showdown result split
- bb/100, positional bb/100, session result, standard error/variance
- all-in outcome fields only when the required equity/state data exists; never fabricate all-in adjusted EV

## Aggression / line measures
- bet%, raise%, call%, fold% by street/opportunity
- aggression factor `AF = (bets + raises) / calls` with divide-by-zero handling
- aggression frequency with explicitly defined denominator
- double/triple barrel, check-call/check-raise sequences, delayed lines, probe sequences
- raise-c-bet then continue, bet-fold, raise-fold, bet-call, bet-3bet frequencies
- overbet frequencies by street and spot

## Sizing distributions
Store raw bet/raise amount, pot fraction under an explicit pot-before-action convention, geometric size context, and canonical buckets. Statistics should support quantiles/mean/median as well as frequency by bucket.

## Board-conditioned dimensions
Paired/unpaired; monotone/two-tone/rainbow; high-card band; connectedness; straight-density; flush-density; static/dynamic heuristic classes; turn/river card pair/overcard/undercard/flush-complete/straight-complete; nut/range advantage values only when produced by an explicit model.

## Sample and uncertainty display
For any binomial frequency, support raw `x/n`, Beta posterior mean/interval, population prior identity, recent/session/lifetime split, and recency effective sample size. Never show `0%` for `0 opportunities`; use unavailable/no-sample.

## Generated-stat examples
The DSL must express all of these without new SQL/code paths:
- BB fold vs BTN open, 80-120bb, heads-up pot, last 90 days
- BTN flop c-bet 25-40% pot in SRP vs BB on A-high rainbow boards
- OOP turn check-raise after flop check-call on flush-completing turns
- River fold vs 125%+ overbet in 3-bet pots, filtered by effective stack at flop
- SB 3-bet vs BTN open for current session vs lifetime, with posterior interval

## Definition governance
Every base stat has a stable ID, version, description, opportunity predicate, success predicate, legal exclusions, and golden fixtures. Definition changes create a new version and must not silently merge historical aggregates produced by incompatible definitions.
