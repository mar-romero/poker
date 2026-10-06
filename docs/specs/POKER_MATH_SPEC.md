# Poker Mathematics Specification

## Purpose
This document is the normative mathematical contract for authoritative calculations. Every function must state its pot-timing convention, units, assumptions, and numerical tolerance. Monetary/chip accounting must not depend on binary floating point.

## Symbols and conventions
- `P`: pot visible to Hero immediately before Hero's decision, including any opponent bet already made and excluding Hero's pending call/raise contribution.
- `C`: additional amount Hero must contribute to call.
- `B`: Hero bet amount when betting into a pot `P0` where no facing bet exists.
- `q`: Hero showdown equity conditional on the branch being evaluated, including split-pot/tie equity.
- `F`: probability all relevant opponents fold to Hero's bet/raise in the modeled branch.
- All EVs are **incremental from the current decision point**; chips already contributed are sunk.

## Pot odds / required equity
For a terminal call with current pot `P` and call cost `C`:

`required_equity = C / (P + C)`

Ignoring future betting/rake, net incremental call EV is:

`EV(call) = q * P - (1 - q) * C = q * (P + C) - C`

`EV(fold) = 0`

The call is break-even when `q = C / (P + C)`.

## Zero-equity bluff break-even fold rate
Hero bets `B` into pot `P0`, wins `P0` if everyone folds, and loses `B` whenever called with zero equity:

`FE_break_even = B / (P0 + B)`

## Bet EV with one fold-or-call branch
For heads-up equal call amount `B`, no future betting, conditional called equity `q`:

`EV(bet) = F * P0 + (1 - F) * (q * (P0 + B) - (1 - q) * B)`

Equivalent called branch: `q * (P0 + 2B) - B`.

General production EV uses an explicit branch tree rather than forcing every decision into this closed form.

## Minimum defense frequency (MDF)
Against a single bet `B` into pre-bet pot `P0`, under the classical zero-equity-bluff indifference assumptions:

`MDF = P0 / (P0 + B)`

`auto_profit_fold_rate = B / (P0 + B)`

MDF is a theoretical reference, not an automatic exploit recommendation; range advantage, blockers, future streets, multiway play, rake, and non-zero bluff equity can change practical defense.

## Polarized river bluffing ratio
For a polar river bet `B` into `P0`, if bluff hands have zero showdown value when called and value hands always win, caller indifference requires bluff fraction among bets:

`bluff_fraction = B / (P0 + 2B)`

Equivalent bluff:value combo ratio:

`bluffs / value = B / (P0 + B)`

## SPR
At a defined street entry:

`SPR = effective_stack / pot`

The implementation must record *which snapshot* supplies stack and pot; do not compare SPR values produced at different timings without labels.

## Combinatorics
`C(n,k) = n! / (k!(n-k)!)`

Texas Holdem two-card starting combinations: `C(52,2) = 1326`.
For one unblocked rank class before known-card removal:
- pocket pair: 6 combos
- suited unpaired: 4 combos
- offsuit unpaired: 12 combos
- all unpaired: 16 combos

Known cards remove combinations before any range normalization or blocker analysis.

## Weighted range equity
For legal opponent combos `h` with normalized weight `w_h` and hero equity `e_h` after runout integration:

`Equity = sum_h(w_h * e_h)` where `sum_h w_h = 1`.

Multiway equity requires legal *joint* opponent-hand distributions; independent marginal sampling that permits shared cards is invalid.

## Monte Carlo uncertainty
For independent bounded equity samples `X_i` with mean `m` and sample standard deviation `s`:

`SE(m) = s / sqrt(n)`

Confidence reporting must state the method and assumptions. Seeded simulations are required for reproducibility tests. Early stopping may use a configured confidence half-width but must report actual sample count.

## Bayesian frequency estimates
For a binomial statistic with prior `Beta(alpha0, beta0)`, successes `x`, and opportunities `n`:

`posterior = Beta(alpha0 + x, beta0 + n - x)`

`posterior_mean = (alpha0 + x) / (alpha0 + beta0 + n)`

Credible intervals come from Beta quantiles. Raw `x/n` must remain visible; posterior estimates must never masquerade as raw observations.

## Exponential recency weighting
For observation age `t` and half-life `H` in the same unit:

`w(t) = exp(-ln(2) * t / H)`

Weighted effective sample size for weights `w_i`:

`n_eff = (sum w_i)^2 / sum(w_i^2)`

## EV loss / regret for training
For chosen action `a` and best modeled action value `V*` under the same model snapshot:

`modeled_ev_loss = max(0, V* - V(a))`

If action values overlap within uncertainty/tolerance, the grader must not create false precision; mark the decision as equivalent/uncertain as configured.

## Rake and fees
Rake is a versioned function of the completed pot/venue rules. Any threshold/EV formula that includes rake must state the schedule and cannot reuse no-rake closed forms blindly. `rake=0` must reduce exactly to base formulas.

## Additional mathematical modules to include
- side-pot and chip-conservation identities
- effective stack / amount-to-call / min-raise rules
- pot-growth and geometric sizing utilities
- implied-odds threshold helpers with explicit future-win assumptions
- reverse-implied-odds scenario analysis (not a single magic scalar)
- variance / standard error of bb/100 and session results
- Brier score and calibration curves for predicted action/fold probabilities
- KL/Jensen-Shannon or other bounded distribution-distance diagnostics where useful
- threshold/sensitivity analysis for range/fold/equity uncertainty
- numerical tolerance policy for solver and simulation outputs (see "Precision and bb-denominated presentation" below; further solver/simulation tolerances extend this normative base)

## Test oracle policy
Each formula must have: (1) hand-calculated fixtures, (2) algebraic identity tests, (3) invalid-domain tests, and where practical (4) independent-library/oracle cross-checks. Approximate numerical methods require explicit absolute/relative tolerances.

## Precision and bb-denominated presentation
This section is the normative precision contract for all authoritative monetary/chip calculations (`poker.math.precision`).

### Units and arithmetic domains
- Authoritative chip amounts are exact non-negative plain ints carried by `Chips`; binary floats never carry an authoritative amount and never enter pot/stack/EV arithmetic.
- bb (big-blind) denominated values are `decimal.Decimal`; unit is big-blind chips unless stated otherwise; when a bb amount references a stake, the stake's `bb` value (chip units) is part of the contract.
- Every Decimal computation runs inside a module-local decimal context with `PRECISION_CONTEXT_PREC = 50` significant digits and deliberately widened exponent bounds; the process-global decimal context is never mutated or relied upon. Raw `int / int` true division is forbidden in the precision layer because it silently yields a binary float; conversions divide `Decimal` values inside the module context.

### bb conversion
- `to_bb(chips, bb)`: exact ratio `Decimal(chips) / Decimal(bb)` in bb units; no rounding inside the conversion (non-terminating ratios carry full context precision). Inputs use strict chip validation (Chips or plain int only); `bb <= 0` and negative chips fail with a raised error.
- `from_bb(amount_bb, bb)`: reconstructs integer chips only when `amount_bb * bb` is exactly an integral non-negative value; a lossy reconstruction must raise rather than silently round the authoritative chip amount. `amount_bb` is accepted only as a Decimal instance or a decimal string.
- `format_bb(chips, bb, places=2)`: user-facing presentation only — `to_bb` then `round_bb` under the PRESENTATION policy, formatted as a fixed-places string. Presentation strings must never be parsed back for arithmetic; rebuild exact amounts with `from_bb`.

### Rounding policies
Rounding is always explicit and named; the implicit default `ROUND_HALF_EVEN` is never applied:
- `PRESENTATION` (ROUND_HALF_UP): user-facing bb display; `.5` boundaries round away from zero.
- `CONSERVATIVE_ALLOCATION` (ROUND_DOWN): allocation-faced display/quantization where no value may be over-credited by rounding.
- `round_bb(value, places=2, policy=PRESENTATION)`: quantize to `places` fractional digits inside the module context (`prec=50` avoids `InvalidOperation` at extreme magnitudes).

### Chip allocation (largest remainder)
`allocate_exact(weights, total)` partitions `total` across weights so the returned parts sum EXACTLY to `total` (chip conservation identity):
- `share_i = total * w_i / W` with `W = sum(weights)`, computed as an exact rational (Fraction or context-local Decimal).
- Each share is floored to whole chips; the leftover `total - sum(floors)` is distributed one chip at a time to the shares with the largest fractional remainders, deterministically tie-broken by lowest index first.
- Rounded parts are never re-summed and re-rounded. Validation: non-negative weights/total with strict chip typing; `total > 0` with all-zero weights is undefined and raises; `total == 0` yields all `Chips(0)`.

### bb serialization (authority separation)
`bb_to_dict` / `bb_from_dict` serialize a bb Decimal with exactly the keys `schema_version`, `value`, `unit`:
- `value` is always the full-precision decimal string `str(Decimal)` (never a JSON number and never a float); `unit` is always `'bb'`; `schema_version` follows the domain's single version source.
- `bb_from_dict` accepts only a decimal `str` for `value` and fails closed on missing/extra keys, a wrong unit, malformed or non-finite literals (`NaN`, `Infinity`, `-Infinity`) or a foreign schema version; `bb_to_dict` equally refuses to serialize a non-finite Decimal.
- A bb dict is NOT parseable by `Chips.from_dict`; chip and bb payloads are separate authorities and must never flow through each other's parsers.

### Tolerance policy for outbound float use
- Exact-domain comparisons (int/int, Decimal/Decimal) are exact; tolerances never replace them.
- `Tolerance(abs_tol, rel_tol)` bundles an explicit absolute and relative tolerance; both are mandatory non-negative finite Decimal instances (floats/ints/bools/strings rejected).
- `decimal_to_float(value)` is the ONE documented one-way outbound conversion (`float(value)`), used only to leave the exact domain for genuinely float-domain boundaries. Rebuilding a Decimal (or Chips) from a float is forbidden.
- `float_within(a, b, tol)` implements `|a - b| <= abs_tol + rel_tol * |b|` in the float domain only, with `b` as the reference/oracle side; authoritative calculations must not pass through it.
