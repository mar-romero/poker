"""Per-combo classification of made-hand strength, pair tiers, draws and
showdown value over the canonical 1,326-combo lattice.

Task POKER-RANGE-CLASSES-001. Public API (frozen by the planner handoff):

- ``COMBO_CLASSES_VERSION = '1.0.0'``: semantic-version constant. Bump on any
  vocabulary or field-semantics change, never on internal refactors. Any
  product-level relabeling is a vocabulary-value change behind a minor/major
  version bump (``1.1.0+``), never a silent reinterpretation of a ``1.0.0``
  label.
- ``ComboClass``: frozen dataclass with EXACTLY these fields, in this order:
  ``board_size`` (int, 3/4/5), ``strength`` (``STRENGTH_LABELS``),
  ``pair_class`` (``PAIR_CLASS_LABELS``), ``overcards`` (int), and the
  booleans ``flush_draw``, ``backdoor_flush_draw``, ``straight_draw``,
  ``backdoor_straight_draw``, ``combo_draw``, ``nut_flush_draw``,
  ``second_nut_flush_draw``; ``showdown_value`` (``SHOWDOWN_LABELS``).

- ``classify_combo(combo, board) -> ComboClass``: the per-combo primitive.
- ``classify_range(rng, board) -> dict[tuple[Card, Card], ComboClass]``:
  keys are exactly ``enumerate_starting_hands(dead_cards=board)`` (strict
  ``DECK`` i<j order; C(49,2)=1176 flop / C(48,2)=1128 turn / C(47,2)=1081
  river live combos). Classification is weight-independent: the ``Range``
  appears only as an accepted argument type (validated by strict ``type()``
  identity) and is never conditioned or mutated — weights stay the caller's
  job via ``rng.weight``. The board facts are derived ONCE per board and
  reused across all combos (see ``_BoardFacts``); per-combo work is pure
  card-derived arithmetic over public ``Card`` domain attributes.
- ``class_masses(rng, board) -> dict[str, dict[str, Fraction]]``: exactly
  three zero-filled families ``'strength'``, ``'pair_class'`` and
  ``'showdown_value'`` keyed by the closed vocabularies; each label value is
  the exact ``Fraction`` sum of ``rng.weight(combo)`` over the combos carrying
  that label (via ``classify_range``), so each family's masses sum EXACTLY
  (==, no float) to ``rng.total_mass()`` for range inputs whose dead-combo
  weights are zero (the well-formed intake for a board-partition argument).
  The zero range yields all ``Fraction(0)`` with sum ``0 == total_mass``, no
  error. Zero-filled keys follow the ``Range.class_groups`` precedent so
  vocabulary additions stay additive.

Vocabularies (closed sets)
--------------------------
- ``STRENGTH_LABELS`` (9): ``('high-card', 'pair', 'two-pair', 'trips',
  'straight', 'flush', 'full-house', 'quads', 'straight-flush')``, mirroring
  ``evaluator.HandCategory`` names (``HIGH_CARD=1`` through
  ``STRAIGHT_FLUSH=9``).
- ``PAIR_CLASS_LABELS`` (7): ``('better-than-one-pair', 'overpair',
  'top-pair', 'second-pair', 'third-pair-or-lower', 'underpair',
  'no-made-pair')``.
- ``SHOWDOWN_LABELS`` (3): ``('above-board-best', 'ties-board-best',
  'not-showdown-street')``.

Semantics (flop n=3 / turn n=4 / river n=5; all facts are card-derived —
no strategic labels, no equity, no opponent modeling)
----------------------------------------------------------------
Strength (mirrors ``docs/specs/RANGE_EQUITY_SPEC.md:52-53``): the category of
the best 5-card hand formed by any 5 of combo+board INCLUDING at least one
hole card, via the pinned street ladder:

- flop (n=3): the single 5-card set ``evaluate(combo + board)``;
- turn (n=4): the maximum ``evaluation_key`` over the SIX 5-card subsets of
  ``combo + board`` through ``evaluate`` (``evaluate7`` requires EXACTLY
  seven unique Cards and must never be called on 5/6-card totals);
- river (n=5): ``evaluate7(combo + board)`` directly. The board-only
  candidate merely ties and can never dominate the hole-inclusive maximum,
  so the hole-inclusive max is well-defined without enumerating subsets.

``r*`` (pair tier reference): the highest BOARD rank with multiplicity
exactly 1; sentinel ``0`` when no singleton rank exists (all-paired/quads
boards). With ``r* == 0`` the 'top-pair'/'second-pair' branches below are
practically unreachable because any hole pairing fires step 1 as trips or
two-pair; the sentinel exists so step 2 stays total (made-hand pocket pairs
land in ``'better-than-one-pair'`` at step 1 anyway).

Pair class, decided in PINNED order (first match wins):

1. ``strength`` in ``{'two-pair', 'trips', 'straight', 'flush', 'full-house',
   'quads', 'straight-flush'}`` -> ``'better-than-one-pair'``;
2. pocket pair (both hole ranks equal ``r``): ``r > r*`` -> ``'overpair'``,
   ``r == r*`` -> ``'top-pair'``, ``r < r*`` -> ``'underpair'``;
3. exactly ONE hole card pairing a multiplicity-1 board rank ``p`` (if both
   hole cards pair singleton ranks the hand is already two-pair and step 1
   fired; pairing a multiplicity>=2 board rank is already trips and step 1
   fired): ``p == r*`` -> ``'top-pair'``, ``p == second-highest singleton
   rank`` -> ``'second-pair'``, else ``'third-pair-or-lower'``;
4. otherwise ``'no-made-pair'``.

Overcards: the count of DISTINCT hole ranks strictly greater than ``r0``,
where ``r0 = r*`` when ``r* > 0`` else the top board rank (sentinel
fallback, e.g. trips-on-board). Pocket pairs contribute at most one distinct
rank.

Flush facts (street gates are part of the definition, not assertions):
for each suit ``s`` let ``k = board_count(s)`` and ``c = combo_count(s)``:

- ``flush_draw`` := ``c + k == 4 AND n < 5`` (exactly one runout card of
  that suit completes the flush; on river the flush is final);
- ``backdoor_flush_draw`` := ``c + k == 3 AND n == 3`` (exactly two runouts
  needed; turn/river have too few remaining streets);
- a made 5-flush (``c + k == 5``) falls in neither branch, so the
  exclusivity of 'no draw label on a made flush' is ARITHMETIC, not asserted
  (a made flush never co-occurs with ``flush_draw`` /
  ``backdoor_flush_draw``, and the nut booleans — which require
  ``flush_draw`` — are therefore False on a made flush as well, even when
  the hole holds the suit Ace).

Straight facts: the TEN canonical windows are the nine five-strength runs
``{2..6}`` .. ``{10..14}`` plus the wheel ``{A, 2, 3, 4, 5}`` counted 5-high
(never 14-high), exactly mirroring ``poker.cards.board``. A window's
'missing' count is the number of its five strengths absent from the UNION of
board ranks and hole ranks (the opponent-blocker complement is deliberately
ignored — blocker-conditioned nuttiness is BLOCKERS-001 territory):

- ``straight_draw`` := ``n < 5`` AND exists a window with exactly 1 missing
  strength AND no complete window over board+combo (the made-straight
  suppression gate is a PINNED consistency choice: on e.g. the 3s-4d-5c-6h
  turn board with hole 7d-2c, strength is ``'straight'`` while the 8-high
  straight sits one card away, yet ``straight_draw`` is False because the
  {3..7} window is already complete);
- ``backdoor_straight_draw`` := ``n == 3`` AND exists a window with exactly
  2 missing strengths AND no complete window (the same complete-window
  suppression gate). It may coexist with ``straight_draw`` on a flop when
  both 1-missing and 2-missing windows exist; exclusivity between the two
  is deliberately NOT pinned in 1.0.0;
- ``combo_draw`` := ``flush_draw AND straight_draw``.

Nut booleans (the DOCUMENTED card-identity exception to suit isomorphism,
exactly paralleling ``board.RunoutDelta``): consider only the suit whose
``c + k == 4`` (the '4-flush suit'; at most one such suit exists):

- ``nut_flush_draw`` := ``flush_draw`` AND the hole holds the ACE of the
  4-flush suit;
- ``second_nut_flush_draw`` := ``flush_draw`` AND the hole holds the KING of
  the 4-flush suit AND NOT ``nut_flush_draw`` (holding BOTH the Ace and King
  of that suit -> nut True, second False: the pin prefers the nut tier).
  Both imply ``flush_draw`` and are mutually exclusive. All other nuttiness
  (current-nuts over hypothetical holdings, nut-straight tiers) is deferred
  to BLOCKERS-001; every field other than these two booleans is
  suit-isomorphism-invariant.

Showdown value: on river,
``compare(evaluate7(combo + board), evaluate(board))`` with ``1`` ->
``'above-board-best'`` and ``0`` -> ``'ties-board-best'``; the hole-inclusive
maximum weakly dominates the board-only evaluation by construction, so
'below-board-best' is unreachable and deliberately EXCLUDED from the closed
vocabulary. On flop/turn the partition label is always
``'not-showdown-street'`` (exactly ``SHOWDOWN_LABELS`` covers every combo at
every street).

Fail-closed validation (mirrors the ``evaluator``/``board``/``model``
precedents): ``combo`` must be a sequence of exactly two distinct ``Card``
instances (wrong arity raises ``TypeError`` per the model precedent;
``strict type() is Card`` identity so subclasses are rejected as
``TypeError``; a duplicate card inside the combo raises ``ValueError``);
``board`` must be a sequence of exactly 3/4/5 unique ``Card`` instances
(same strict style: ``ValueError`` for wrong count or duplicates,
``TypeError`` for non-``Card`` items); any combo card already on the board
is impossible and raises ``ValueError``; ``classify_range``/``class_masses``
raise ``TypeError`` for a first argument not EXACTLY
``poker.range.model.Range`` by ``type()`` identity, and ``ValueError`` for
an invalid board.

Purity policy: deterministic, pure functions of their inputs; no floats, no
probabilities, no randomness, no global mutable state, no caching, no
strategic labels, no equity; exact ``Fraction`` arithmetic only in
``class_masses``.

Semantics are frozen by the committed planner handoff
(POKER-RANGE-CLASSES-001), pinned by tests/range/test_classes.py with
in-test oracles only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

from poker.cards.evaluator import HandCategory, compare, evaluate, evaluate7
from poker.domain.cards import RANKS, Card
from poker.math.combinatorics import enumerate_starting_hands
from poker.range.model import Range

__all__ = [
    "COMBO_CLASSES_VERSION",
    "ComboClass",
    "classify_combo",
    "classify_range",
    "class_masses",
]

COMBO_CLASSES_VERSION = "1.0.0"

STRENGTH_LABELS: tuple[str, ...] = (
    "high-card",
    "pair",
    "two-pair",
    "trips",
    "straight",
    "flush",
    "full-house",
    "quads",
    "straight-flush",
)

PAIR_CLASS_LABELS: tuple[str, ...] = (
    "better-than-one-pair",
    "overpair",
    "top-pair",
    "second-pair",
    "third-pair-or-lower",
    "underpair",
    "no-made-pair",
)

SHOWDOWN_LABELS: tuple[str, ...] = (
    "above-board-best",
    "ties-board-best",
    "not-showdown-street",
)


@dataclass(frozen=True)
class ComboClass:
    """Immutable, card-derived classification of one combo on one board."""

    board_size: int
    strength: str
    pair_class: str
    overcards: int
    flush_draw: bool
    backdoor_flush_draw: bool
    straight_draw: bool
    backdoor_straight_draw: bool
    combo_draw: bool
    nut_flush_draw: bool
    second_nut_flush_draw: bool
    showdown_value: str


# Rank strength: '2'..'A' -> 2..14, re-derived from the public RANKS order.
_RANK_STRENGTH: dict[str, int] = {rank: index + 2 for index, rank in enumerate(RANKS)}

# The ten canonical straight windows: nine five-strength runs {2..6}..{10..14}
# plus the wheel {A, 2, 3, 4, 5} counted 5-high (never 14-high). Re-declared
# locally from the same public definition as board.py (which exposes no suit/
# window API): both lists derive from range(2, 11) + the wheel constant, so a
# source change in one file cannot silently desynchronize the other.
_STRAIGHT_WINDOWS: tuple[frozenset[int], ...] = tuple(
    frozenset(range(start, start + 5)) for start in range(2, 11)
) + (frozenset((14, 2, 3, 4, 5)),)
assert len(_STRAIGHT_WINDOWS) == 10

_STRENGTH_BY_CATEGORY: dict[HandCategory, str] = {
    HandCategory.HIGH_CARD: "high-card",
    HandCategory.PAIR: "pair",
    HandCategory.TWO_PAIR: "two-pair",
    HandCategory.THREE_OF_A_KIND: "trips",
    HandCategory.STRAIGHT: "straight",
    HandCategory.FLUSH: "flush",
    HandCategory.FULL_HOUSE: "full-house",
    HandCategory.FOUR_OF_A_KIND: "quads",
    HandCategory.STRAIGHT_FLUSH: "straight-flush",
}

# Pinned step-1 set of the pair-class recipe.
_BETTER_THAN_ONE_PAIR = frozenset(
    {"two-pair", "trips", "straight", "flush", "full-house", "quads", "straight-flush"}
)


def _validate_two_card_combo(source: object, api_name: str) -> tuple[tuple[Card, Card], frozenset[str]]:
    """Validate a two-Card combo (arity/type/duplicate fail-closed pin).

    Returns the ordered pair and its distinct-symbol set. A non-sequence or
    wrong arity raises ``TypeError`` (model.py:99-104 precedent); non-``Card``
    items raise ``TypeError`` (strict ``type()`` identity, subclasses
    rejected); a duplicate card inside the combo raises ``ValueError``.
    """
    try:
        items = list(source)
    except TypeError:
        raise TypeError(
            f"{api_name} must be a sequence of two Card objects, "
            f"got {type(source).__name__}"
        ) from None
    if len(items) != 2:
        raise TypeError(
            f"{api_name} must contain exactly two Card objects, got {len(items)}"
        )
    for card in items:
        if type(card) is not Card:
            raise TypeError(
                f"{api_name} expects Card instances, got {type(card).__name__}"
            )
    symbols = frozenset(card.symbol for card in items)
    if len(symbols) != 2:
        raise ValueError(f"{api_name} received duplicate cards")
    return (items[0], items[1]), symbols


def _validate_cards(source: object, allowed_sizes: tuple[int, ...], api_name: str) -> list[Card]:
    """Validate a Card sequence in the pinned board.py-style matrix.

    ``ValueError`` for a count outside ``allowed_sizes`` or duplicate cards;
    ``TypeError`` for non-``Card`` items (strict ``type()`` identity).
    """
    try:
        items = list(source)
    except TypeError:
        raise TypeError(
            f"{api_name} must be a sequence of Card objects, "
            f"got {type(source).__name__}"
        ) from None
    expected = " or ".join(str(size) for size in allowed_sizes)
    if len(items) not in allowed_sizes:
        raise ValueError(
            f"{api_name} requires exactly {expected} cards, got {len(items)}"
        )
    for card in items:
        if type(card) is not Card:
            raise TypeError(
                f"{api_name} expects Card instances, got {type(card).__name__}"
            )
    symbols = {card.symbol for card in items}
    if len(symbols) != len(items):
        raise ValueError(f"{api_name} received duplicate cards")
    return items


def _overlapping_combo(symbol_pair: frozenset[str], board_cards: list[Card]) -> bool:
    """True when the combo touches the board (impossible hole cards)."""
    return not symbol_pair.isdisjoint(card.symbol for card in board_cards)


class _BoardFacts:
    """Board facts derived ONCE per validated board, reused per combo.

    Pure card-derived arithmetic over public ``Card`` attributes: the rank
    multiplicity map (class_groups precedent), per-suit board counts
    (BoardTexture is deliberately suit-label-free, so suit counts are
    re-derived here from ``Card.suit``), the singleton ranks with the
    ``r*``/``r0`` sentinels, and the per-window board-missing counts of the
    ten canonical straight windows.
    """

    __slots__ = (
        "n",
        "board_cards",
        "board_symbols",
        "rank_counts",
        "board_strengths",
        "suit_counts",
        "r_star",
        "second_singleton",
        "r0",
        "window_missing",
    )

    def __init__(self, board_cards: list[Card]) -> None:
        self.n: int = len(board_cards)
        self.board_cards: list[Card] = board_cards
        self.board_symbols: frozenset[str] = frozenset(
            card.symbol for card in board_cards
        )
        self.rank_counts: dict[int, int] = dict(
            Counter(_RANK_STRENGTH[card.rank] for card in board_cards)
        )
        self.board_strengths: frozenset[int] = frozenset(self.rank_counts)
        self.suit_counts: dict[str, int] = dict(Counter(card.suit for card in board_cards))
        singletons = sorted(
            (rank for rank, m in self.rank_counts.items() if m == 1), reverse=True
        )
        self.r_star: int = singletons[0] if singletons else 0
        self.second_singleton: int | None = singletons[1] if len(singletons) > 1 else None
        self.r0: int = self.r_star if self.r_star > 0 else max(self.rank_counts)
        self.window_missing: list[int] = [
            5 - len(window & self.board_strengths) for window in _STRAIGHT_WINDOWS
        ]


def _strength_evaluation(combo: tuple[Card, Card], facts: _BoardFacts):
    """Best hole-inclusive evaluation through the pinned street ladder.

    Flop: the single 5-card set through ``evaluate``; turn: the maximum
    ``evaluation_key`` over the six 5-card subsets through ``evaluate``
    (never ``evaluate7`` on 6 cards); river: ``evaluate7`` directly. On
    river the returned evaluation doubles as the showdown side of the
    pinned compare (the hole-inclusive max weakly dominates the board-only
    evaluation by construction).
    """
    cards = [combo[0], combo[1], *facts.board_cards]
    if facts.n == 3:
        return evaluate(cards)
    if facts.n == 4:
        best = None
        best_key: tuple[int, tuple[int, ...]] | None = None
        for subset in combinations(cards, 5):
            candidate = evaluate(subset)
            candidate_key = candidate.evaluation_key()
            if best_key is None or candidate_key > best_key:
                best = candidate
                best_key = candidate_key
        assert best is not None  # six subsets for six distinct cards
        return best
    return evaluate7(cards)


def _pair_class_label(strength: str, combo: tuple[Card, Card], facts: _BoardFacts) -> str:
    """The pinned 4-step pair-class recipe (first match wins)."""
    if strength in _BETTER_THAN_ONE_PAIR:
        return "better-than-one-pair"
    hole_a = _RANK_STRENGTH[combo[0].rank]
    hole_b = _RANK_STRENGTH[combo[1].rank]
    if hole_a == hole_b:  # step 2: pocket pair against r*
        if hole_a > facts.r_star:
            return "overpair"
        if hole_a == facts.r_star:
            return "top-pair"
        return "underpair"
    # step 3: exactly one hole card pairing a multiplicity-1 board rank
    pairing_singletons = [
        rank for rank in (hole_a, hole_b) if facts.rank_counts.get(rank, 0) == 1
    ]
    if len(pairing_singletons) == 1:
        p = pairing_singletons[0]
        if p == facts.r_star:
            return "top-pair"
        if p == facts.second_singleton:
            return "second-pair"
        return "third-pair-or-lower"
    return "no-made-pair"  # step 4


def _overcards_count(combo: tuple[Card, Card], facts: _BoardFacts) -> int:
    """DISTINCT hole ranks strictly greater than r0 (sentinel fallback)."""
    distinct_hole = {_RANK_STRENGTH[combo[0].rank], _RANK_STRENGTH[combo[1].rank]}
    return sum(1 for rank in distinct_hole if rank > facts.r0)


def _flush_facts(combo: tuple[Card, Card], facts: _BoardFacts) -> tuple[bool, bool, bool, bool]:
    """(flush_draw, backdoor_flush_draw, nut_flush_draw, second_nut_flush_draw).

    The made 5-flush (sum 5) falls in neither branch: exclusivity is
    arithmetic, and the nut booleans — which require ``flush_draw`` — are
    False on a made flush even when the hole holds the suit Ace. Note that
    the sum-4 (and therefore nut-relevant) suit is unique: suit sums total
    n + 2 <= 6, so two suits cannot both reach 4. The nut booleans are the
    documented card-identity exception to suit isomorphism.
    """
    combo_suits = Counter(card.suit for card in combo)
    flush_draw = False
    backdoor_flush_draw = False
    four_flush_suit: str | None = None
    for suit, board_count in facts.suit_counts.items():
        suit_sum = board_count + combo_suits.get(suit, 0)
        if suit_sum == 4 and facts.n < 5:
            flush_draw = True
            four_flush_suit = suit
        elif suit_sum == 3 and facts.n == 3:
            backdoor_flush_draw = True
    nut_flush_draw = False
    second_nut_flush_draw = False
    if flush_draw:
        holds_ace = any(
            card.suit == four_flush_suit and card.rank == "A" for card in combo
        )
        holds_king = any(
            card.suit == four_flush_suit and card.rank == "K" for card in combo
        )
        nut_flush_draw = holds_ace
        second_nut_flush_draw = holds_king and not holds_ace
    return flush_draw, backdoor_flush_draw, nut_flush_draw, second_nut_flush_draw


def _straight_facts(combo: tuple[Card, Card], facts: _BoardFacts) -> tuple[bool, bool]:
    """(straight_draw, backdoor_straight_draw) with the suppression gate.

    Window missing counts are re-based from the board-only precomputation by
    the hole strengths (present = board ranks UNION hole ranks; opponent
    blockers deliberately ignored). The complete-window gate suppresses both
    labels — the pinned made-straight consistency choice.
    """
    hole_strengths = {_RANK_STRENGTH[combo[0].rank], _RANK_STRENGTH[combo[1].rank]}
    one_missing = False
    two_missing = False
    complete = False
    for board_missing, window in zip(
        facts.window_missing, _STRAIGHT_WINDOWS, strict=True
    ):
        missing = board_missing - sum(
            1
            for rank in hole_strengths
            if rank in window and rank not in facts.board_strengths
        )
        if missing == 0:
            complete = True
        elif missing == 1:
            one_missing = True
        elif missing == 2:
            two_missing = True
    straight_draw = facts.n < 5 and one_missing and not complete
    backdoor_straight_draw = facts.n == 3 and two_missing and not complete
    return straight_draw, backdoor_straight_draw


def _classify_validated(pair: tuple[Card, Card], facts: _BoardFacts) -> ComboClass:
    """Full classification of an already-validated pair (pure, per street)."""
    best = _strength_evaluation(pair, facts)
    strength = _STRENGTH_BY_CATEGORY[best.category]
    if facts.n == 5:
        verdict = compare(best, evaluate(facts.board_cards))
        showdown_value = "above-board-best" if verdict > 0 else "ties-board-best"
    else:
        showdown_value = "not-showdown-street"
    flush_draw, backdoor_flush_draw, nut, second = _flush_facts(pair, facts)
    straight_draw, backdoor_straight_draw = _straight_facts(pair, facts)
    return ComboClass(
        board_size=facts.n,
        strength=strength,
        pair_class=_pair_class_label(strength, pair, facts),
        overcards=_overcards_count(pair, facts),
        flush_draw=flush_draw,
        backdoor_flush_draw=backdoor_flush_draw,
        straight_draw=straight_draw,
        backdoor_straight_draw=backdoor_straight_draw,
        combo_draw=flush_draw and straight_draw,
        nut_flush_draw=nut,
        second_nut_flush_draw=second,
        showdown_value=showdown_value,
    )


def classify_combo(combo, board) -> ComboClass:
    """Classify one two-card combo against a flop/turn/river board.

    ``combo`` must be a sequence of exactly two distinct ``Card`` instances
    and ``board`` exactly 3/4/5 unique ``Card`` instances; a combo card
    already on the board is impossible and fails closed. Pure, deterministic,
    no floats, no state.
    """
    pair, combo_symbols = _validate_two_card_combo(combo, "classify_combo combo")
    board_cards = _validate_cards(board, (3, 4, 5), "classify_combo board")
    if _overlapping_combo(combo_symbols, board_cards):
        raise ValueError(
            "classify_combo received a combo card that is already on the board "
            "(impossible combo)"
        )
    return _classify_validated(pair, _BoardFacts(board_cards))


def classify_range(rng, board) -> dict[tuple[Card, Card], ComboClass]:
    """Classify every canonical combo disjoint from ``board``.

    Keys are exactly ``enumerate_starting_hands(dead_cards=board)`` in its
    strict DECK i<j order. ``rng`` must be exactly ``poker.range.model.Range``
    (strict ``type()`` identity); classification is weight-independent — the
    range is never conditioned, mutated or read for weights here.
    """
    if type(rng) is not Range:
        raise TypeError(
            f"classify_range expects exactly a poker.range.model.Range, "
            f"got {type(rng).__name__}"
        )
    board_cards = _validate_cards(board, (3, 4, 5), "classify_range board")
    facts = _BoardFacts(board_cards)
    return {
        pair: _classify_validated(pair, facts)
        for pair in enumerate_starting_hands(dead_cards=board_cards)
    }


def class_masses(rng, board) -> dict[str, dict[str, Fraction]]:
    """Exact zero-filled per-family label masses for ``rng`` on ``board``.

    Three families (``'strength'``, ``'pair_class'``, ``'showdown_value'``)
    keyed exactly by the closed vocabularies; each label value is the exact
    ``Fraction`` sum of ``rng.weight(combo)`` over the combos classified with
    that label, so each family's masses sum EXACTLY to ``rng.total_mass()``
    whenever the range's weights outside the live lattice are zero (the
    well-formed board-partition intake). The zero range yields all
    ``Fraction(0)`` with sum ``0 == total_mass``, no error.
    """
    if type(rng) is not Range:
        raise TypeError(
            f"class_masses expects exactly a poker.range.model.Range, "
            f"got {type(rng).__name__}"
        )
    _validate_cards(board, (3, 4, 5), "class_masses board")
    classification = classify_range(rng, board)
    families: dict[str, dict[str, Fraction]] = {
        "strength": {label: Fraction(0) for label in STRENGTH_LABELS},
        "pair_class": {label: Fraction(0) for label in PAIR_CLASS_LABELS},
        "showdown_value": {label: Fraction(0) for label in SHOWDOWN_LABELS},
    }
    for combo, cc in classification.items():
        weight = rng.weight(combo)
        families["strength"][cc.strength] += weight
        families["pair_class"][cc.pair_class] += weight
        families["showdown_value"][cc.showdown_value] += weight
    return families
