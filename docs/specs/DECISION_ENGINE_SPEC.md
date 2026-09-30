# Decision Engine Specification

## Objective
Produce an educational, auditable decision analysis from a canonical `DecisionSpot`. The engine does not output a bare "best move" without context; it returns candidate actions, mixed-strategy frequencies where supported, modeled EV, uncertainty, sensitivity, strategy provenance, and a reasoning trace.

## Inputs
- immutable game snapshot: pot, stacks, amount to call, min/max raise, positions, street, board, action history
- hero cards when known
- legal action/sizing set
- hero/opponent weighted ranges and range-inference provenance
- player statistics with raw samples and posterior uncertainty
- population priors/cohorts
- baseline strategy source and abstraction distance
- rake/fee schedule
- compute/latency budget

## Source hierarchy
1. Exact terminal math / exact enumeration when applicable.
2. Valid high-quality exact/precomputed strategy for matching information set.
3. Converged/benchmarked local solver result with approximation metadata.
4. Abstract strategy interpolation with distance/confidence.
5. Direct EV model with explicit branch assumptions.
6. Heuristic educational guidance only when higher-quality models are unavailable, clearly labeled.

## Output contract
For each candidate action: legality, sizing, baseline frequency, exploit-adjusted frequency, EV estimate, uncertainty, branch contributions, key range/equity inputs, and sensitivity. Overall output includes confidence, freshness, stale/missing inputs, and provenance IDs.

## Exploit layer
Exploit adjustments require: a named baseline comparison, enough effective sample, confidence threshold, bounded adjustment magnitude, and a trace. Low-sample raw rates never directly force large strategy shifts.

## Mixed strategies
Close actions may remain mixed. A deterministic UI can display the target mix and optionally a seeded pedagogical randomizer, but stored analysis must retain the full distribution rather than only one sampled action.

## Sensitivity
Test plausible ranges of fold/call/raise frequencies, equity, range composition, and sizing response. If action ranking changes within plausible inputs, mark the recommendation fragile and identify threshold variables.

## Failure behavior
Missing/stale state, impossible ranges, unconverged solver, or contradictory evidence must degrade to explicit `insufficient_model` / `uncertain` / `stale` status. Never fill missing inputs with invisible guesses.
