"""Blocker-aware dead-card reports over the canonical two-card combo lattice.

Durable specification for task POKER-RANGE-BLOCKERS-001 (semantics frozen by
the committed planner handoff, POKER-RANGE-BLOCKERS-001).

Public API
----------
- ``BLOCKERS_VERSION = '1.0.0'``: semantic-version constant. Bump on any
  report-field or semantics change, never on internal refactors.
- ``BlockerReport``: frozen dataclass with EXACTLY these fields, in this
  order: ``board`` (the validated board tuple, caller order preserved),
  ``dead_cards`` (the board + ``known_cards`` union in canonical ``DECK``
  order), ``live_combo_count`` (int), ``class_counts``
  (dict[str, int] over ``{'pair', 'suited', 'offsuit'}``),
  ``class_removal_delta`` (dict[str, int], ``class_counts(())`` minus
  ``class_counts(dead)`` per key), ``value_counts`` (dict[str, Fraction]
  over the ``classes.STRENGTH_LABELS`` vocabulary, zero-filled),
  ``pair_class_counts`` (dict[str, Fraction] over
  ``classes.PAIR_CLASS_LABELS``, zero-filled), ``draw_int_counts``
  (dict[str, int]) and ``draw_masses`` (dict[str, Fraction]) over the five
  ``ComboClass`` booleans (``flush_draw``, ``backdoor_flush_draw``,
  ``straight_draw``, ``backdoor_straight_draw``, ``combo_draw``), and
  ``nut_availability`` (dict[str, int] with exactly the keys
  ``'nut_flush_draw'`` and ``'second_nut_flush_draw'``).
- ``blocker_analysis(rng, board, known_cards=()) -> BlockerReport``.

Intake and validation (fail closed)
-----------------------------------
- ``rng`` must satisfy strict ``type(rng) is Range`` identity (mirroring
  ``classes.classify_range``/``class_masses``); ``Range`` subclasses and
  every other object raise ``TypeError``.
- ``board`` must be a sequence of exactly 3, 4 or 5 unique ``Card``
  instances: a non-sequence board raises ``TypeError``, a wrong
  cardinality raises ``ValueError``, and element/duplicate validation
  follows ``cards.assert_unique_cards`` semantics (``TypeError`` for
  non-Card entries, ``ValueError`` for duplicates).
- ``known_cards`` is any sequence of 0..47 additional ``Card`` instances;
  the dead set is the UNION ``board + known_cards`` and must satisfy
  ``assert_unique_cards`` semantics as a whole, so a known card already on
  the board, or a duplicate inside ``known_cards``, raises ``ValueError``.

Semantics
---------
- Structural facts are computed once over the dead set:
  ``live_combo_count == len(enumerate_starting_hands(dead))``,
  ``class_counts == combinatorics.class_counts(dead)`` and
  ``class_removal_delta == {k: class_counts(())[k] - class_counts(dead)[k]}``
  (a pure function of the union dead set; there is no separate
  known-cards-only decomposition). ``dead_cards`` is stored in canonical
  ``DECK`` order, independent of caller input order.
- Weighted facts follow the ignore-dead-weights contract: the dead set is
  fixed before any counting, classification comes from
  ``classes.classify_range(rng, board)`` over the board-disjoint lattice,
  and weights are read as ``rng.weight(combo)`` while iterating the
  dead-disjoint live lattice ``enumerate_starting_hands(dead)`` (never by
  iterating rng keys). Combos outside the classification lattice
  (board-touching) and combos touching any dead card therefore contribute
  exactly 0 to every weighted fact, which makes the result identical
  whether or not the caller pre-conditioned the range with the same dead
  set; blockers.py never calls ``Range.condition`` and never mutates
  ``rng``. ``value_counts``/``pair_class_counts`` start from
  ``classes.class_masses(rng, board)`` with the mass of known-card-touching
  lattice combos removed, so for a range pre-conditioned with the same dead
  set they equal ``class_masses`` verbatim.
- Reconciliation identities (exact Fraction ``==``, no floats): for any
  range pre-conditioned with the same dead set,
  ``sum(value_counts.values()) == rng.total_mass()`` and
  ``sum(pair_class_counts.values()) == rng.total_mass()`` (the
  board-partition intake of ``class_masses`` is satisfied because
  conditioning zeroes every board/known-card-touching combo). No such
  identity is claimed for draw facts.
- Draw facts are OVERLAPPING boolean labels, NOT a partition:
  ``combo_draw`` is the conjunction of ``flush_draw`` and
  ``straight_draw``, so ``sum(draw_masses.values())`` has no conservation
  meaning and must never be compared against ``total_mass()``.
  ``draw_int_counts`` are weight-independent structural counts of live
  lattice combos whose flag is True; ``draw_masses`` weight the same flags
  by ``rng.weight``.
- ``nut_availability`` is weight-independent: exact counts of live lattice
  combos whose ``nut_flush_draw`` / ``second_nut_flush_draw`` classification
  boolean is True.
- Zero range: every Fraction field is ``Fraction(0)``, no error; integer
  structural counts are still fully computed.
- Purity: no floats, no randomness, no mutation of ``rng``; every ordering
  derives from canonical ``DECK`` order and the pinned closed vocabularies;
  two calls on identical inputs return equal reports with identical
  ``repr``.

Deferred to 1.1.0 (explicitly out of scope here): nut-straight tier
availability, per-combo "unblocked by" card-identity facts, showdown_value
family masses, and any probability/float outputs.
"""

from __future__ import annotations

import dataclasses
from fractions import Fraction

from poker.domain.cards import Card, DECK
from poker.math.combinatorics import enumerate_starting_hands, class_counts, combinations_count
from poker.range.model import Range
from poker.range import classes

__all__ = ["BLOCKERS_VERSION", "BlockerReport", "blocker_analysis"]

BLOCKERS_VERSION = "1.0.0"


@dataclasses.dataclass(frozen=True)
class BlockerReport:
    """Frozen, explanation-ready dead-card report (pinned field set)."""

    board: tuple
    dead_cards: tuple
    live_combo_count: int
    class_counts: dict[str, int]
    class_removal_delta: dict[str, int]
    value_counts: dict[str, Fraction]
    pair_class_counts: dict[str, Fraction]
    draw_int_counts: dict[str, int]
    draw_masses: dict[str, Fraction]
    nut_availability: dict[str, int]


#: The five ``ComboClass`` draw booleans reported by ``draw_int_counts`` and
#: ``draw_masses`` (overlapping boolean facts, deliberately NOT a partition).
_DRAW_FLAGS: tuple[str, ...] = (
    "flush_draw",
    "backdoor_flush_draw",
    "straight_draw",
    "backdoor_straight_draw",
    "combo_draw",
)

#: Structural class keys of ``combinatorics.class_counts`` (closed set).
_CLASS_KEYS: tuple[str, str, str] = ("pair", "suited", "offsuit")

#: The pinned ``nut_availability`` keys (weight-independent counts).
_NUT_KEYS: tuple[str, str] = ("nut_flush_draw", "second_nut_flush_draw")


def _validated_board(board) -> tuple[Card, ...]:
    """Validate the board intake: 3/4/5-card sequence (fail closed).

    A non-sequence board raises ``TypeError``; a wrong cardinality raises
    ``ValueError``. Element type and duplicate validation follow the
    ``cards.assert_unique_cards`` semantics delegated through
    ``combinatorics.enumerate_starting_hands`` on the whole dead union (a
    non-Card board entry raises ``TypeError`` there with the union label).
    """
    try:
        cards = tuple(board)
    except TypeError:
        raise TypeError(
            "blocker_analysis board must be a sequence of Card objects, "
            f"got {type(board).__name__}: {board!r}"
        ) from None
    if len(cards) not in (3, 4, 5):
        raise ValueError(
            "blocker_analysis board must contain exactly 3, 4 or 5 Cards, "
            f"got {len(cards)}"
        )
    return cards


def blocker_analysis(rng, board, known_cards=()) -> BlockerReport:
    """Build the frozen blocker report for ``rng`` behind ``board`` plus
    ``known_cards`` (validation and semantics per the module docstring).

    Pure and deterministic: ``rng`` is never conditioned or mutated, no
    floats are produced, and every ordering derives from canonical ``DECK``
    order and the pinned closed vocabularies.
    """
    if type(rng) is not Range:
        raise TypeError(
            "blocker_analysis expects exactly a poker.range.model.Range, "
            f"got {type(rng).__name__}"
        )
    board_cards = _validated_board(board)
    known = tuple(known_cards)
    dead = board_cards + known
    # assert_unique_cards semantics for the whole union (TypeError for
    # non-Card entries, ValueError for duplicates within board, within
    # known_cards and across the two sequences) is delegated to
    # combinatorics, which applies the same guard before enumerating the
    # live lattice; the union dead set is fixed before any counting.
    lattice = enumerate_starting_hands(dead)
    dead_cards = tuple(card for card in DECK if card in set(dead))

    structural = class_counts(dead)
    full = class_counts(())
    class_removal_delta = {
        key: full[key] - structural[key] for key in _CLASS_KEYS
    }

    classification = classes.classify_range(rng, board_cards)
    masses = classes.class_masses(rng, board_cards)
    value_counts = dict(masses["strength"])
    pair_class_counts = dict(masses["pair_class"])
    # Ignore-dead-weights: remove the mass that known-card-touching combos
    # contribute to the class_masses board-partition sums, so every weighted
    # fact reads weights over the dead-disjoint live lattice only. Board-
    # touching combos are outside the classification lattice and were never
    # summed; for a range pre-conditioned with the same dead set the masses
    # equal class_masses verbatim (nothing to remove).
    known_set = set(known)
    if known_set:
        for combo, cc in classification.items():
            if combo[0] in known_set or combo[1] in known_set:
                weight = rng.weight(combo)
                value_counts[cc.strength] -= weight
                pair_class_counts[cc.pair_class] -= weight

    draw_int_counts: dict[str, int] = {flag: 0 for flag in _DRAW_FLAGS}
    draw_masses: dict[str, Fraction] = {flag: Fraction(0) for flag in _DRAW_FLAGS}
    nut_availability: dict[str, int] = {key: 0 for key in _NUT_KEYS}
    for combo in lattice:
        cc = classification[combo]
        weight = rng.weight(combo)
        for flag in _DRAW_FLAGS:
            if getattr(cc, flag):
                draw_int_counts[flag] += 1
                draw_masses[flag] += weight
        if cc.nut_flush_draw:
            nut_availability["nut_flush_draw"] += 1
        if cc.second_nut_flush_draw:
            nut_availability["second_nut_flush_draw"] += 1

    return BlockerReport(
        board=board_cards,
        dead_cards=dead_cards,
        live_combo_count=len(lattice),
        class_counts=structural,
        class_removal_delta=class_removal_delta,
        value_counts=value_counts,
        pair_class_counts=pair_class_counts,
        draw_int_counts=draw_int_counts,
        draw_masses=draw_masses,
        nut_availability=nut_availability,
    )
