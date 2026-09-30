# Range and Equity Specification

## Canonical range space
Holdem ranges operate over the 1,326 unordered two-card combinations. A range maps each legal combo to a weight in `[0,1]`; conditioning on known cards removes impossible combos before normalization. Hand-class notation is only an input/output convenience, not the storage truth.

## Range lifecycle
1. Establish prior from position/stack/action strategy or population model.
2. Remove dead cards (hero cards, board, known exposed cards).
3. Apply action/sizing likelihood evidence as versioned likelihood multipliers.
4. Normalize and retain a trace of prior, likelihood, posterior, model version, and evidence.
5. Recondition after every new public card/action.

The platform must support both frequency-style weights and normalized probability mass, labeling which representation is being shown.

## Board model
Board features include rank multiplicities, suit multiplicities, high-card band, gaps/connectivity, available straight patterns, flush states, paired/trips/quads state, and turn/river transition deltas. Suit isomorphism may be used only where blocker/nut effects are preserved by the abstraction.

## Combo classes
For explanation and aggregation, classify combos into made-hand strength, pair tier, overcards, straight draws, flush draws, combo draws, nuttiness, showdown-value bucket, and blocker properties. Draw labels must respect street (e.g. no future draw on river).

## Equity engines
- Exact enumeration whenever state space is within configured budget.
- Seeded Monte Carlo otherwise.
- Range-vs-range and multiway engines must sample legal joint holdings without shared cards.
- Ties split pot equity correctly.
- Every result includes method, sample/enumeration count, seed if sampled, range versions, dead cards, and uncertainty where applicable.

## Caching
Cache keys include hero/range hashes, board/dead cards, method, sample budget/seed policy, evaluator version, and game/rake context where it affects EV. Never reuse equity across incompatible range or board versions.
