"""Exact river heads-up equity by full enumeration (durable spec).

Semantics are frozen by the committed planner handoff for
POKER-EQUITY-EXACT-001; the committed test-designer handoff is the test
specification and ``tests/equity/test_exact.py`` pins every behavior below
with an in-test brute-force oracle only.

v1.0.0 scope
------------
River-only, heads-up, EXACT equity: a fixed hero two-card hole pair and
exactly five unique board Cards (the river) are compared, card-by-card over
every live opponent combo, against a weighted :class:`poker.range.model.Range`
over the live combos. The live deck is ``DECK`` minus the known cards
(hero + board + ``dead_cards``), enumerated by the public
``poker.math.combinatorics.enumerate_starting_hands(dead_cards=known)`` in
strict DECK ``i < j`` deterministic order — every live combo is evaluated
EXACTLY once (no sampling, no double-counting, no RNG). With 7 known cards
the live deck is 45 cards, so the enumeration is C(45, 2) = 990 combos and
roughly 990 ``evaluate7`` calls (hero evaluation is hoisted out of the loop):
seconds-scale and tractable exactly.

Out-of-scope pins (v1.0.0)
--------------------------
- Multiway equity is OUT; it requires JOINT distributions over opponent
  hands and is owned by POKER-EQUITY-MULTI-001
  (docs/specs/POKER_MATH_SPEC.md L83).
- Pre-river exact enumeration (turn/flop all-street runout enumeration) is
  OUT of v1.0.0 and deferred to 1.1.0 (a per-runout design multiplies the
  river cost by C(44, 2) = 946 runouts with no amortized board evaluation;
  turn-level range conditioning is a distinct API surface). Pre-river
  estimation is owned by POKER-EQUITY-MC-001 (sampling).
- Sampling is OUT of this module entirely (no RNG import, by pinned
  static-purity test).
- A float-based API is OUT: all weights, masses and result fields are exact
  ``fractions.Fraction`` values (binary floats and ``Decimal`` never enter).
- A per-combo equity map is OUT (KISS: a caller can recompute per-combo
  equities from the evaluator plus combinatorics; no speculative
  infrastructure).

Accumulation semantics
----------------------
Weights are normalized over the total LIVE mass only:
``w_norm(combo) = rng.weight(combo) / total_live_mass`` where
``total_live_mass`` is the exact sum of ``rng.weight`` over the enumerated
live combos ONLY. Weights on combos containing hero/board/dead cards are
silently excluded (ignore-dead-weights precedent from range blockers dead
handling and ``Range.condition`` zeroing dead entries) — they contribute 0
and are not an error. This local normalization is deliberately NOT
``Range.normalized_weights()``, which divides by the mass over ALL 1,326
combos (a global denominator would silently misstate equity whenever the
range carries mass on impossible combos). ``sum(w_norm) == 1`` exactly.
Each combo contributes its ``w_norm`` to exactly one of ``hero_wins``
(compare = +1), ``ties`` (compare = 0) or ``opponent_wins`` (compare = -1);
the invariant ``hero_wins + ties + opponent_wins == Fraction(1)`` holds
exactly, and ``equity = hero_wins + ties / 2`` (each tying player receives
half the pot heads-up).

Fail-closed validation
----------------------
- ``hero`` and ``board`` items are validated with STRICT ``type(card) is
  Card`` identity (subclasses rejected, ``TypeError``), mirroring
  ``poker.range.classes._validate_cards``; ``hero`` must contain exactly two
  unique Cards and ``board`` exactly five unique Cards (``ValueError`` on
  wrong arity or duplicates; river-only pin).
- ``dead_cards`` validation DELEGATES to
  ``poker.domain.cards.assert_unique_cards`` semantics via the
  ``enumerate_starting_hands`` call (``isinstance``-based ``TypeError`` for
  non-Card entries, ``ValueError`` for duplicates). This is the committed
  test-designer D3 resolution of the planner handoff's tension between the
  strict-identity convention (hero/board) and the ``assert_unique_cards``
  delegate (dead): strict identity for hero/board, ``isinstance`` delegate
  for dead_cards.
- Hero, board and dead_cards must be pairwise disjoint
  (``ValueError`` on any overlap).
- ``opponent_range`` must be exactly :class:`poker.range.model.Range`
  (strict ``type`` identity, ``TypeError`` otherwise, naming
  ``poker.range.model.Range``).
- A range with zero mass on the live combos — including the zero range and
  a lattice left empty by dead-card saturation — fails closed with
  ``ValueError('opponent range has no mass on live combos')`` (mirroring the
  zero-range fail-closed precedent of ``Range.normalized_weights``).
- ``enumerated_opponent_combos`` counts are asserted in tests, not at
  runtime.

Purity: the ``Range`` value object is never mutated; the lattice order is
deterministic for identical inputs; two calls with equal inputs return equal
``EquityResult`` values; no sampling/RNG import is permitted in this module.

Version bump rule: any change to this signature or semantics bumps
``EQUITY_EXACT_VERSION`` and updates this docstring; earlier callers are
never silently reinterpreted.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Sequence

from poker.cards.evaluator import compare, evaluate7
from poker.domain.cards import Card
from poker.math.combinatorics import enumerate_starting_hands
from poker.range.model import Range

__all__ = ["EQUITY_EXACT_VERSION", "EquityResult", "exact_river_equity"]

EQUITY_EXACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class EquityResult:
    """Exact river-equity outcome (frozen value object).

    ``equity = hero_wins + ties / 2`` with every Fraction field exact and in
    ``[0, 1]``; ``hero_wins + ties + opponent_wins == Fraction(1)`` exactly;
    ``enumerated_opponent_combos`` is the count of live combos evaluated.
    """

    equity: Fraction
    hero_wins: Fraction
    ties: Fraction
    opponent_wins: Fraction
    enumerated_opponent_combos: int


def _validated_strict_cards(
    source: object, label: str, expected_size: int
) -> tuple[Card, ...]:
    """Strict-identity card-sequence validation (fail closed).

    Mirrors ``poker.range.classes._validate_cards``: ``TypeError`` for a
    non-sequence or any item that is not exactly a ``Card`` (subclasses
    rejected), ``ValueError`` for a wrong arity or duplicate cards.
    """
    try:
        items = list(source)
    except TypeError:
        raise TypeError(
            f"{label} must be a sequence of Card objects, "
            f"got {type(source).__name__}"
        ) from None
    if len(items) != expected_size:
        raise ValueError(
            f"{label} requires exactly {expected_size} cards, got {len(items)}"
        )
    for card in items:
        if type(card) is not Card:
            raise TypeError(
                f"{label} expects Card instances, got {type(card).__name__}"
            )
    symbols = {card.symbol for card in items}
    if len(symbols) != expected_size:
        raise ValueError(f"{label} received duplicate cards")
    return tuple(items)


def exact_river_equity(
    hero: Sequence[Card],
    board: Sequence[Card],
    opponent_range: Range,
    dead_cards: Sequence[Card] = (),
) -> EquityResult:
    """Exact heads-up river equity of ``hero`` against a weighted range.

    See the module docstring for the durable specification: full enumeration
    of every live opponent combo in strict DECK ``i < j`` order, live-mass
    weight normalization, exact Fraction accumulation, tie split 1/2, and
    fail-closed validation. Raises ``TypeError`` for non-Card items in
    ``hero``/``board`` (strict identity), non-Card items in ``dead_cards``
    (``assert_unique_cards`` delegate) and a non-``Range``
    ``opponent_range``; ``ValueError`` for wrong hero/board cardinality,
    duplicates, pairwise overlaps and zero mass on the live combos.
    """
    hero_cards = _validated_strict_cards(hero, "hero", 2)
    board_cards = _validated_strict_cards(board, "board", 5)
    _fail_on_disjoint(set(hero_cards), "hero", set(board_cards), "board")
    if type(opponent_range) is not Range:
        raise TypeError(
            "opponent_range must be a poker.range.model.Range instance, "
            f"got {type(opponent_range).__name__}"
        )
    dead_items = tuple(dead_cards)
    hero_symbols = {card.symbol for card in hero_cards}
    board_symbols = {card.symbol for card in board_cards}
    for card in dead_items:
        symbol = getattr(card, "symbol", None)
        if symbol is None:
            continue  # non-Card entries fail in the delegated validation
        if symbol in hero_symbols:
            raise ValueError(f"dead card {symbol!r} overlaps the hero hand")
        if symbol in board_symbols:
            raise ValueError(f"dead card {symbol!r} overlaps the board")
    known = hero_cards + board_cards + dead_items
    lattice = enumerate_starting_hands(dead_cards=known)
    total_live_mass = Fraction(0)
    for combo in lattice:
        total_live_mass += opponent_range.weight(combo)
    if total_live_mass == 0:
        raise ValueError("opponent range has no mass on live combos")
    hero_eval = evaluate7(hero_cards + board_cards)
    hero_wins = Fraction(0)
    ties = Fraction(0)
    opponent_wins = Fraction(0)
    for combo in lattice:
        weight = opponent_range.weight(combo) / total_live_mass
        outcome = compare(hero_eval, evaluate7(combo + board_cards))
        if outcome > 0:
            hero_wins += weight
        elif outcome == 0:
            ties += weight
        else:
            opponent_wins += weight
    return EquityResult(
        equity=hero_wins + ties / 2,
        hero_wins=hero_wins,
        ties=ties,
        opponent_wins=opponent_wins,
        enumerated_opponent_combos=len(lattice),
    )


def _fail_on_disjoint(
    left: set[Card], left_label: str, right: set[Card], right_label: str
) -> None:
    """Raise ``ValueError`` when two validated card sets share any card."""
    overlap = sorted(card.symbol for card in left & right)
    if overlap:
        raise ValueError(
            f"{left_label} and {right_label} must be pairwise disjoint, "
            f"overlapping card(s): {', '.join(overlap)}"
        )
