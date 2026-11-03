"""Exact combinational math for Texas Hold'em starting-hand combos.

This module is the combinatorial layer of the exact-math stack: it counts and
enumerates unordered two-card combos over the canonical ``DECK`` from
``poker.domain.cards`` and computes exact, unnormalized combo mass with
``fractions.Fraction`` arithmetic only (binary floats and ``Decimal`` are
deliberately excluded from all authoritative paths here).

Dead-card concept
-----------------
Board cards and opponent/blocker cards are ONE dead-card-set concept; the
range epic passes board + known hole cards as one ``dead_cards`` set. A dead
card is removed from the live deck before any count or enumeration; the
``class_combo``/structural-class inputs never add or remove dead cards.

Structural classes (no strategic heuristics)
--------------------------------------------
For an unordered pair ``(a, b)`` of distinct cards, the structural class is
determined purely by rank/suit equality:

- ``pair``:   ``a.rank == b.rank``                              (6 per rank class)
- ``suited``: ``a.rank != b.rank and a.suit == b.suit``          (4 per two-rank class)
- ``offsuit``:``a.rank != b.rank and a.suit != b.suit``         (12 per two-rank class)

Over the full 52-card deck the aggregates are ``{'pair': 78, 'suited': 312,
'offsuit': 936}`` and the total unordered two-card combo count is
``combinations_count(52, 2) == 1326``.

Enumeration order pin
---------------------
``enumerate_starting_hands`` returns ``tuple(itertools.combinations(live, 2))``
where ``live`` preserves canonical ``DECK`` index order, so every pair satisfies
``DECK.index(a) < DECK.index(b)`` (strict ``i < j``). The output order is
deterministic across calls for identical ``dead_cards``.

Weight / mass / normalization policy
------------------------------------
``weighted_combo_mass`` returns the exact unnormalized Fraction sum of the
given integer or ``Fraction`` weights over structurally valid, mutually
distinct (unordered identity) combos. The module never normalizes: the caller
divides mass / all-mass with exact ``Fraction`` division. Integer weights
yield results with denominator 1; empty input yields ``Fraction(0)``;
zero-weight combos are legal and contribute nothing.

Fail-closed counting note
-------------------------
``combinations_count`` is deliberately stricter than ``math.comb``:
``math.comb(n, k)`` returns ``0`` for ``k > n``, but this module raises
``ValueError`` for ``n < 0``, ``k < 0`` and ``k > n`` so that impossible or
inconsistent requests fail loudly instead of silently returning 0.

Scope note: the human-readable range notation ``'AA'`` / ``'AKs'`` /
``'AKo'`` is deliberately deferred to the range-notation work unit; this
module operates on ``(Card, Card)`` pairs only.

Semantics are frozen by the committed planner handoff for
``POKER-MATH-COMBOS-001``; the committed test-designer handoff is the test
specification and ``tests/math/test_combinatorics.py`` pins every behavior
below with in-test oracles only.
"""

from __future__ import annotations

from fractions import Fraction
from itertools import combinations
from math import comb
from typing import Sequence

from poker.domain.cards import DECK, Card, assert_unique_cards

__all__ = [
    "combinations_count",
    "enumerate_starting_hands",
    "class_counts",
    "class_remaining_count",
    "weighted_combo_mass",
]

def _require_plain_int(value: object, label: str) -> int:
    """Fail-closed integer guard: only ``type(value) is int`` is accepted.

    ``bool`` is deliberately rejected even though ``type(True)`` subclasses
    ``int``, mirroring the repository's strict-identity validation style: a
    boolean is not an ``n``/``k``/weight quantity.
    """
    if type(value) is not int:
        raise TypeError(
            f"{label} must be a plain int, got {type(value).__name__}: {value!r}"
        )
    return value


def _validated_live_cards(dead_cards: Sequence[Card]) -> list[Card]:
    """Validate ``dead_cards`` and return the live deck in ``DECK`` order.

    Delegates type/duplicate validation to ``cards.assert_unique_cards``
    (``TypeError`` for non-Card entries, ``ValueError`` for duplicates; both
    acceptable per the repository VALIDATION convention) and filters the
    canonical ``DECK`` while preserving its index order. The frozen ``Card``
    dataclass identity (hashable ``(rank, suit)``) makes set filtering exact.
    """
    assert_unique_cards(dead_cards)
    dead = set(dead_cards)
    return [card for card in DECK if card not in dead]


def _validated_pair(pair: object, label: str) -> tuple[Card, Card]:
    """Validate a two-Card pair argument (arity, type, duplicate card)."""
    try:
        items = list(pair)
    except TypeError:
        raise TypeError(
            f"{label} must be a sequence of two Card objects, "
            f"got {type(pair).__name__}: {pair!r}"
        ) from None
    if len(items) != 2:
        raise TypeError(
            f"{label} must contain exactly two Card objects, got {len(items)}"
        )
    assert_unique_cards(items)
    return (items[0], items[1])


def _structural_class(a: Card, b: Card) -> str:
    """Return the structural class of the unordered pair ``(a, b)``."""
    if a.rank == b.rank:
        return "pair"
    if a.suit == b.suit:
        return "suited"
    return "offsuit"


def combinations_count(n: int, k: int) -> int:
    """Return the exact number of unordered ``n``-choose-``k`` combinations.

    Deliberately stricter than ``math.comb``: impossible or inconsistent
    requests fail closed instead of silently returning 0 (``math.comb``
    returns 0 for ``k > n``).
    """
    _require_plain_int(n, "n")
    _require_plain_int(k, "k")
    if n < 0 or k < 0:
        raise ValueError(f"n and k must be non-negative, got n={n!r}, k={k!r}")
    if k > n:
        raise ValueError(
            f"k must not exceed n, got n={n!r}, k={k!r} "
            "(fail-closed: stricter than math.comb, which returns 0 for k > n)"
        )
    return comb(n, k)


def enumerate_starting_hands(
    dead_cards: Sequence[Card] = (),
) -> tuple[tuple[Card, Card], ...]:
    """Return every unordered live two-card combo in strict ``DECK`` i<j order.

    ``DECK`` index order is preserved (``itertools.combinations`` emits
    ``i < j`` pairs over the live-deck filter of ``DECK``), so the enumeration
    is deterministic. Natural set semantics: with fewer than two live cards
    the result is the empty tuple ``()``.
    """
    live = _validated_live_cards(dead_cards)
    return tuple(combinations(live, 2))


def class_counts(dead_cards: Sequence[Card] = ()) -> dict[str, int]:
    """Return exact ``{'pair', 'suited', 'offsuit'}`` counts over live combos."""
    counts = {"pair": 0, "suited": 0, "offsuit": 0}
    for a, b in combinations(_validated_live_cards(dead_cards), 2):
        counts[_structural_class(a, b)] += 1
    return counts


def class_remaining_count(
    dead_cards: Sequence[Card],
    class_combo: tuple[Card, Card],
) -> int:
    """Return the count of live unordered combos in ``class_combo``'s class.

    The class is determined from ``class_combo`` by rank participation plus
    structure: equal ranks define the pair class of that one rank (6 combos
    full deck); distinct ranks define the suited class (4 full deck) or the
    offsuit class (12 full deck) of exactly those two ranks. ``class_combo``
    only defines the class — its cards are allowed to be dead themselves and
    are never treated as removed.
    """
    live = _validated_live_cards(dead_cards)
    high, low = _validated_pair(class_combo, "class_combo")
    rank_pair = frozenset((high.rank, low.rank))
    same_rank = high.rank == low.rank
    same_suit = high.suit == low.suit
    remaining = 0
    for a, b in combinations(live, 2):
        if frozenset((a.rank, b.rank)) != rank_pair:
            continue
        if (a.rank == b.rank) != same_rank:
            continue
        if (a.suit == b.suit) != same_suit:
            continue
        remaining += 1
    return remaining


def weighted_combo_mass(
    combos: Sequence[tuple[Card, Card]],
    weights: Sequence[int | Fraction],
) -> Fraction:
    """Return the exact unnormalized Fraction mass for validated combos/weights.

    All intake validation happens before any summation: length mismatch
    (``ValueError``), weight type (``int``/``Fraction`` only; ``bool``, float,
    ``Decimal`` and strings raise ``TypeError``), negative weights
    (``ValueError``), per-entry two-distinct-Card structure (``TypeError`` /
    ``ValueError``), and duplicate combos by unordered card-set identity
    (``(a, b)`` and ``(b, a)`` are the same structural combo:
    ``ValueError``). The exact sum starts at ``Fraction(0)``; integer weights
    contribute with denominator 1, empty input yields ``Fraction(0)``, and
    zero-weight entries are legal. The module never normalizes: the caller
    divides mass / all-mass with exact ``Fraction`` division.
    """
    combo_items = list(combos)
    weight_items = list(weights)
    if len(combo_items) != len(weight_items):
        raise ValueError(
            "combos and weights must have equal length, "
            f"got {len(combo_items)} combos != {len(weight_items)} weights"
        )
    seen_keys: list[frozenset[str]] = []
    for index, entry in enumerate(combo_items):
        a, b = _validated_pair(entry, f"combos[{index}]")
        key = frozenset((a.symbol, b.symbol))
        if key in seen_keys:
            raise ValueError(
                f"duplicate unordered combo {a.symbol}/{b.symbol} "
                f"at position {index}"
            )
        seen_keys.append(key)
    for index, weight in enumerate(weight_items):
        if type(weight) is not int and not isinstance(weight, Fraction):
            raise TypeError(
                f"weights[{index}] must be an int or a Fraction, "
                f"got {type(weight).__name__}: {weight!r}"
            )
        if weight < 0:
            raise ValueError(
                f"weights[{index}] must be non-negative, got {weight!r}"
            )
    total = Fraction(0)
    for weight in weight_items:
        total += weight
    return total
