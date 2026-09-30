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
