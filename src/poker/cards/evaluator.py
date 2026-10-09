"""Deterministic poker hand evaluation for 5-card and 7-card hands.

Task POKER-HAND-EVAL-001. Public API (frozen by the planner handoff):

- ``HandCategory``: ``IntEnum`` ordered ascending by strength, ``HIGH_CARD=1``
  through ``STRAIGHT_FLUSH=9``.
- ``HandEvaluation``: frozen dataclass with fields ``category`` (HandCategory),
  ``tiebreak`` (tuple[int, ...], the category-specific canonical key, see
  below) and ``cards`` (the best five cards, canonically ordered: rank strength
  descending; among equal-strength cards suits ascend in ``SUITS`` index order
  of ``poker.domain.cards``, i.e. ``'c' < 'd' < 'h' < 's'``), plus the method
  ``evaluation_key()`` returning ``(category.value, tiebreak)`` for stable,
  cache-friendly comparability.
- ``evaluate(cards)``: accepts an iterable of exactly 5 unique ``Card``
  instances; raises ``ValueError`` on wrong count or duplicates and
  ``TypeError`` on non-``Card`` items. Order-independent and deterministic.
- ``evaluate7(cards)``: exactly 7 unique ``Cards``; returns the best of the 21
  five-card subsets. Ties are resolved deterministically via canonical input
  ordering, so any input permutation yields the identical ``HandEvaluation``.
- ``compare(a, b)``: ``-1/0/1`` comparison of two ``HandEvaluation`` objects
  via ``evaluation_key`` tuple ordering.
- ``EVALUATOR_VERSION = '1.0.0'``: semantic version constant required in
  equity cache keys by docs/specs/RANGE_EQUITY_SPEC.md:29. Bump on any
  ranking-semantics change, not on internal refactors.

Canonical tiebreak shapes: straight/straight-flush ``(high,)`` with the wheel
A2345 counted as 5-high (never 14-high); quads ``(quad, kicker)``; full house
``(trips, pair)``; flush five ranks descending; trips ``(trip, k1, k2)``; two
pair ``(hi, lo, kicker)``; pair ``(pair, k1, k2, k3)``; high card five ranks
descending.

Implementation: straightforward rank counting plus sorted-rank slices, falling
through the strength ladder. No randomness, no global mutable state, no
caching; every call is a pure function of its input. Production and oracle
paths were deliberately kept algorithmically distinct (the test module's
oracle uses a definition-driven predicate ladder over a rank-count multiset).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import IntEnum

from poker.domain.cards import SUITS, RANKS, Card

__all__ = [
    "EVALUATOR_VERSION",
    "HandCategory",
    "HandEvaluation",
    "compare",
    "evaluate",
    "evaluate7",
]

EVALUATOR_VERSION = "1.0.0"


class HandCategory(IntEnum):
    """Poker hand categories ordered ascending by strength."""

    HIGH_CARD = 1
    PAIR = 2
    TWO_PAIR = 3
    THREE_OF_A_KIND = 4
    STRAIGHT = 5
    FLUSH = 6
    FULL_HOUSE = 7
    FOUR_OF_A_KIND = 8
    STRAIGHT_FLUSH = 9


# Rank strength: '2'..'A' -> 2..14, derived from the canonical RANKS order.
_RANK_STRENGTH = {rank: index + 2 for index, rank in enumerate(RANKS)}
# Canonical suit tie-order within equal rank strength: SUITS index order.
_SUIT_ORDER = {suit: index for index, suit in enumerate(SUITS)}

_WHEEL_STRENGTHS = (14, 2, 3, 4, 5)


def _canonical_card_key(card: Card) -> tuple[int, int]:
    return (-_RANK_STRENGTH[card.rank], _SUIT_ORDER[card.suit])


@dataclass(frozen=True)
class HandEvaluation:
    """Immutable result of evaluating one five-card poker hand.

    ``cards`` is the canonical best-five tuple: rank strength descending, and
    suits ascending in ``SUITS`` index order (``'c' < 'd' < 'h' < 's'``) among
    equal-strength cards — deterministic and independent of input order.
    """

    category: HandCategory
    tiebreak: tuple[int, ...]
    cards: tuple[Card, ...]

    def evaluation_key(self) -> tuple[int, tuple[int, ...]]:
        """Stable comparable/cache key: ``(category.value, tiebreak)``."""
        return (self.category.value, self.tiebreak)


def _validate_card_list(source, expected_count: int, api_name: str) -> list[Card]:
    cards = list(source)
    if len(cards) != expected_count:
        raise ValueError(
            f"{api_name} requires exactly {expected_count} cards, got {len(cards)}"
        )
    for card in cards:
        if type(card) is not Card:
            raise TypeError(
                f"{api_name} expects Card instances, got {type(card).__name__}"
            )
    if len(set(cards)) != expected_count:
        raise ValueError(f"{api_name} received duplicate cards")
    return cards


def _best_straight_high(distinct_strengths: set[int]) -> int | None:
    """High card of the straight for five distinct strengths, or ``None``.

    The wheel A2345 is 5-high, never 14-high.
    """
    if len(distinct_strengths) != 5:
        return None
    high = max(distinct_strengths)
    low = min(distinct_strengths)
    if high - low == 4:
        return high
    if tuple(sorted(distinct_strengths)) == tuple(sorted(_WHEEL_STRENGTHS)):
        return 5
    return None


def _evaluate_ordered(ordered_cards: list[Card]) -> HandEvaluation:
    """Classify already-validated cards (any order) into a HandEvaluation."""
    counts: dict[int, int] = {}
    suited = True
    first_suit = ordered_cards[0].suit
    for card in ordered_cards:
        strength = _RANK_STRENGTH[card.rank]
        counts[strength] = counts.get(strength, 0) + 1
        if card.suit != first_suit:
            suited = False
    distinct = sorted(counts, reverse=True)
    straight_high = _best_straight_high(set(counts))
    cards = tuple(sorted(ordered_cards, key=_canonical_card_key))

    if len(distinct) == 5:
        if straight_high is not None:
            if suited:
                return HandEvaluation(
                    HandCategory.STRAIGHT_FLUSH, (straight_high,), cards
                )
            return HandEvaluation(HandCategory.STRAIGHT, (straight_high,), cards)
        if suited:
            return HandEvaluation(HandCategory.FLUSH, tuple(distinct), cards)
        return HandEvaluation(HandCategory.HIGH_CARD, tuple(distinct), cards)
    if len(distinct) == 4:  # exactly one pair
        pair = next(strength for strength, count in counts.items() if count == 2)
        kickers = tuple(strength for strength in distinct if strength != pair)
        return HandEvaluation(HandCategory.PAIR, (pair, *kickers), cards)
    if len(distinct) == 3:
        triple = next((strength for strength, count in counts.items() if count == 3), None)
        if triple is not None:  # three of a kind (3, 1, 1)
            kickers = tuple(strength for strength in distinct if strength != triple)
            return HandEvaluation(HandCategory.THREE_OF_A_KIND, (triple, *kickers), cards)
        # two pair (2, 2, 1): the kicker is the single remaining rank.
        pairs = [strength for strength in distinct if counts[strength] == 2]
        kicker = next(strength for strength, count in counts.items() if count == 1)
        return HandEvaluation(HandCategory.TWO_PAIR, (pairs[0], pairs[1], kicker), cards)
    if len(distinct) == 2:
        if 4 in counts.values():  # four of a kind (4, 1)
            quad = next(strength for strength, count in counts.items() if count == 4)
            kicker = next(strength for strength, count in counts.items() if count == 1)
            return HandEvaluation(HandCategory.FOUR_OF_A_KIND, (quad, kicker), cards)
        # full house (3, 2)
        trips = next(strength for strength, count in counts.items() if count == 3)
        pair = next(strength for strength, count in counts.items() if count == 2)
        return HandEvaluation(HandCategory.FULL_HOUSE, (trips, pair), cards)
    raise AssertionError(f"unreachable rank count pattern: {counts!r}")


def evaluate(cards) -> HandEvaluation:
    """Evaluate exactly five unique ``Card`` instances.

    The returned ``HandEvaluation`` carries the category, the canonical
    tiebreak tuple for that category and the canonically ordered card tuple.
    Raises ``ValueError`` for wrong count or duplicate cards and ``TypeError``
    for non-Card items. Deterministic and order-independent.
    """
    validated = _validate_card_list(cards, 5, "evaluate")
    return _evaluate_ordered(validated)


def evaluate7(cards) -> HandEvaluation:
    """Evaluate exactly seven unique ``Card`` instances as the best five.

    Brute-force over the 21 five-card subsets, selecting the maximum by
    ``evaluation_key``. Input cards are sorted canonically before enumeration,
    so tied best subsets resolve deterministically regardless of input order.
    Raises ``ValueError`` for wrong count or duplicates, ``TypeError`` for
    non-Card items.
    """
    validated = _validate_card_list(cards, 7, "evaluate7")
    best: HandEvaluation | None = None
    best_key: tuple[int, tuple[int, ...]] | None = None
    for combo in itertools.combinations(sorted(validated, key=_canonical_card_key), 5):
        candidate = evaluate(combo)
        candidate_key = candidate.evaluation_key()
        if best_key is None or candidate_key > best_key:
            best = candidate
            best_key = candidate_key
    assert best is not None  # exactly 21 subsets for seven distinct cards
    return best


def compare(a: HandEvaluation, b: HandEvaluation) -> int:
    """Three-way compare of two evaluations via ``evaluation_key``.

    Returns ``-1`` when ``a`` loses, ``0`` when they tie, ``1`` when ``a``
    wins. Ties between identical keys compare equal regardless of card
    identity or suit.
    """
    a_key = a.evaluation_key()
    b_key = b.evaluation_key()
    if a_key < b_key:
        return -1
    if a_key > b_key:
        return 1
    return 0
