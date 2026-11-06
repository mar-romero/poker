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

### Texture classification
`poker.cards.board.classify(cards)` accepts an iterable of exactly 3, 4 or 5 unique `Card` instances (street-generic: flop/turn/river covered by one API; `board_size` is the street proxy) and fails closed mirroring the evaluator: `TypeError` on non-`Card` items (strict `type()` identity, so subclasses fail), `ValueError` on wrong count or duplicates. It is pure, order-independent and deterministic: no floats, no probabilities, no randomness, no caching, no strategic labels. Every field is a suit-label-free card-derived fact, so classification is invariant under suit isomorphism: permuting the four suits never changes the returned texture.

The result is a frozen `BoardTexture` carrying `BOARD_TEXTURE_VERSION = '1.0.0'` (bump on any vocabulary or field-semantics change, not on internal refactors) with exactly these fields:

| Field | Value space |
|---|---|
| `board_size` | `int`, one of 3 (flop), 4 (turn), 5 (river) |
| `rank_multiplicities` | `tuple[int, ...]`, the rank multiplicity partition of `board_size`, descending |
| `pairedness` | closed set `('no-pair', 'one-pair', 'two-paired', 'trips', 'full-house', 'quads')`, injected from the partition: `{4}` → quads; `{3, 2}` → full-house; `{3, 1, ...}` → trips; `{2, 2, ...}` → two-paired; `{2, 1, ...}` → one-pair; `{1, ...}` → no-pair. Partition existence is street-bounded by the board size, but the label vocabulary is shared by all streets, so cells emerge exactly when reachable on that street. |
| `suitedness` | closed set `('rainbow', 'two-tone', 'monotone')`, street-generic rule: monotone iff all `board_size` cards share one suit; two-tone iff exactly two distinct suits are present; rainbow iff three or more distinct suits are present |
| `max_suit_count` | `int`, highest count of any single suit (carries the fine structure so `'rainbow'` never hides a 3-flush/4-flush board) |
| `one_away_flush_suits` | `int`, number of suits whose board count equals `board_size - 1` (0..1 on any street) |
| `high_rank` | canonical rank symbol of the highest board rank |
| `high_band` | closed set `('low', 'mid', 'high', 'superhigh')` on the highest board strength h ('2'..=2 … 'A'=14): low h ≤ 9, mid h in {10, 11}, high h in {12, 13}, superhigh h = 14 |
| `straight_windows_available` | `int`, count of canonical windows with at least 3 of their 5 strengths present on the board |
| `straight_windows_complete` | `int`, count of canonical windows with all 5 strengths present; always 0 for flops/turns, in {0, 1} for rivers |

Straight windows: the ten canonical windows are the nine five-strength runs `{2..6}`, `{3..7}`, …, `{10..14}` plus the wheel `{A, 2, 3, 4, 5}`, which is always counted 5-high, never 14-high. A window is *available* iff at least three of its five strengths are on the board: at most 2 missing ranks are always obtainable from the stub because all four copies of any off-board rank are live. A window is *complete* iff all 5 strengths are on the board.

`BoardTexture.texture_key()` returns one hashable canonical tuple of all fields, available for downstream equity cache keys (see `## Caching`; cache-key accommodation for `BOARD_TEXTURE_VERSION` is deferred to the consuming equity task).

### Runout deltas
`poker.cards.board.runout_delta(board, runout_card)` computes the transition facts for exactly one runout card. `board` must be exactly 3 or 4 unique `Card` instances (river boards have no runout); `runout_card` must be a `Card` not already on the board. Fail-closed mirroring `classify` (`TypeError` on non-`Card`, `ValueError` on wrong board size, duplicates or a runout already on the board). No probabilities, no randomness, no caching: every field is a deterministic card-derived fact.

The result is a frozen `RunoutDelta` with `card` (the runout `Card` itself) and `texture_after` (a `BoardTexture` of board + runout for caller convenience), plus these pinned booleans/counts:
- `pairs_board`: the runout rank already appears on the board (covers pair → trips and trips → quads pairing).
- `completes_flush`: the resulting board contains five cards of one suit (reachable only from a 4-card board whose runout-suit count is 4).
- `extends_flush`: `max_suit_count` strictly increases (e.g. flop 3-flush → turn 4-flush); a lone new suit on an already 3-flush board does not extend anything.
- `straight_windows_before` / `straight_windows_after`: available-window counts before and after, so callers see material straight-structure change exactly.
- `completes_board_straight`: `straight_windows_complete` goes 0 → 1 (the wheel edge `A2345` completed by an Ace runout counts as 5-high, never 14-high).

`RunoutDelta` is deliberately card-identity, NOT suit-isomorphism-invariant: it names the runout card, preserving blocker/nut effects. This is the documented spec exception to the board-model isomorphism rule; label-free comparisons must use `texture_after`, which obeys the `BoardTexture` invariance rule.

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
