"""Blocker-report tests for POKER-RANGE-BLOCKERS-001 (cases C01..C30).

Implements the committed test-designer handoff cases verbatim in intent.
Blueprint style mirrors ``tests/range/test_classes.py``: every fixture
construction stays inside test methods, and all blocker-specific oracles are
in-test definition-driven reimplementations:

- O2 dead-set union + canonical ``DECK``-order filter computed in-test from
  the public ``poker.domain.cards.DECK``;
- O2 ``live_combo_count`` via ``combinatorics.combinations_count(52-n, 2)``;
- O2 ``class_removal_delta`` via ``class_counts(())`` minus
  ``class_counts(dead)`` over the in-test-computed dead union;
- O2 ``draw_int_counts``/``draw_masses`` re-aggregated in-test by iterating
  the dead-disjoint lattice ``enumerate_starting_hands(dead)``, reading the
  ``classify_range(rng, board)`` booleans and summing ``rng.weight(combo)``
  per boolean — this catches dead-weight inclusion, missing zero-fill and
  wrong weight gating (straight-window logic itself is deliberately NOT
  re-derived; classification correctness belongs to
  ``tests/range/test_classes.py``);
- O3 ``nut_availability`` fully independent of ``classes.py`` internals:
  in-test suit arithmetic over public ``Card.rank``/``Card.suit`` and
  ``enumerate_starting_hands(dead)`` (flush draw iff board suit count +
  hole suit count == 4 and ``len(board) < 5``; nut iff the hole holds the
  Ace of the 4-flush suit; second-nut iff it holds the King and not the
  Ace), cross-checked against the pinned manual 38/38 integers.

No ``blockers.py`` internals are imported or re-called; only the pinned
public surface (``blocker_analysis``, ``BlockerReport``,
``BLOCKERS_VERSION``) plus public ``Range``/``combinatorics``/``classes``/
``cards`` APIs are used.

Case map (committed test-designer handoff):
C01..C05 exact reconciliation identities (uniform/sparse/sweep
preconditioned fixtures), C06/C07 pinned ignore-dead-weights contracts,
C08/C09 zero range and purity, C10..C15 structural counts/deltas/canonical
order, C16..C19 nut arithmetic and street gates, C20/C21 draw aggregation
and overlap-not-partition guard, C22/C23 determinism and frozen shape,
C24..C28 fail-closed matrix, C29 RED evidence discipline, C30 tuple/list
intake normalization.

Designer-literal deviations (documented; each is a case in the committed
test-designer handoff that contradicts the authoritative planner rule in
the same handoff, resolved in favor of the planner AC pin, following the
house precedent of ``tests/range/test_classes.py``):

- C01 ``total_mass() == Fraction(1)``: ``Range.uniform(()).condition(board)``
  has total mass exactly ``1176/1326 == 196/221``, not 1. Both readings are
  asserted: the normalized uniform-over-live-lattice fixture
  (``Range.uniform(dead).condition(dead)``, total mass exactly 1) carries
  the pinned ``Fraction(1)`` identity, and the literal designer fixture is
  additionally checked against its own total mass (the identities are
  total-relative, so both hold).
- C01/C13 ``class_removal_delta == {pair: 78-0, suited: 312-0, offsuit:
  936-3}``: this literal contradicts the planner AC verbatim
  (``class_counts(())`` minus ``class_counts(dead)``). For dead Kd 7h 2c
  the removed 150 live combos decompose as 9 pair + 36 suited + 105
  offsuit (13*C(4,2)=78 pairs full-deck, minus the 9 pairs touching a dead
  card; one dead card per suit removes 12 same-suit partners each), so the
  mechanically pinned delta is ``{'pair': 9, 'suited': 36, 'offsuit':
  105}`` and ``class_counts(dead) == {'pair': 69, 'suited': 276,
  'offsuit': 831}``. Pinned per the planner AC; C13 re-derives the same
  arithmetic through the O2 oracle.
- C07 construction: ``Range.uniform(()).condition(dead)`` leaves all 1,128
  dead-disjoint combos at 1/1326 each, so ``total_mass() == 1/1326`` is
  arithmetically impossible for that literal construction. The sparse
  zero-base construction (``Range().set_weight(dead_combo, 1/1326)``)
  realizes the pinned intent — total mass exactly 1/1326 carried ONLY by a
  dead-touching combo — with all weighted report facts exactly
  ``Fraction(0)`` (house precedent: ``tests/range/test_classes.py`` C24).
- C02 ``live_combo_count == 1081`` and C10 "5 (turn, 1081)": a bare turn
  board is dead size 4, so the planner AC verbatim
  (``combinations_count(52 - len(dead), 2)``) pins C(48,2) = 1128; the
  1081 figure is C(47,2) and belongs to dead size 5 (river board). C10
  realizes dead size 5 with the river board.
- C16/C17 pinned "second_nut_flush_draw = 38 (Ks + non-spade mate)": the
  King of the 4-flush suit IS on this board (3s 7s Ks 2h), so no live
  combo can hold it; both the production ``classes.py`` semantics
  (``second_nut`` = holds the King of the four-flush suit, not the Ace)
  and the designer's own O3 oracle definition ("second-nut iff
  flush_draw, holds the King, not the Ace") therefore give 0. The O3
  oracle is kept authoritative and cross-checked against production; the
  38/38 manual literal is corrected to 38/0 (C16) and 0/0 (C17, Ace also
  dead). The flush_draw count/mass literals stay valid: 380 live combos
  hold exactly one spade (10 live spades x 38 live non-spades), mass
  380/1128 for the uniform fixture.
- C03/B8 "straight-side draw facts remain live" on river: per the
  production ``classes.py`` street gates, ``straight_draw`` requires
  ``n < 5`` and ``backdoor_straight_draw`` requires ``n == 3``, so ALL five
  draw booleans are structurally False at n=5. The O2 oracle — which
  derives from those same public booleans — also aggregates 0; C03/C19
  therefore pin the flush-side zeros and nut zeros as literals and assert
  the straight-side facts by exact equality with the O2 oracle.
"""

from __future__ import annotations

import dataclasses
import itertools
import unittest
from collections import Counter
from fractions import Fraction
from pathlib import Path

from poker.domain.cards import DECK, Card
from poker.math.combinatorics import (
    class_counts,
    combinations_count,
    enumerate_starting_hands,
)
from poker.range.blockers import BLOCKERS_VERSION, BlockerReport, blocker_analysis
from poker.range.classes import (
    PAIR_CLASS_LABELS,
    STRENGTH_LABELS,
    classify_range,
    class_masses,
)
from poker.range.model import Range

# ---------------------------------------------------------------------------
# In-test oracles (never importing or re-calling blockers.py internals).
# ---------------------------------------------------------------------------

#: The five ComboClass draw booleans covered by the pinned report fields.
_DRAW_FLAGS: tuple[str, ...] = (
    "flush_draw",
    "backdoor_flush_draw",
    "straight_draw",
    "backdoor_straight_draw",
    "combo_draw",
)

#: The pinned ``nut_availability`` keys.
_NUT_KEYS: tuple[str, ...] = ("nut_flush_draw", "second_nut_flush_draw")

#: Fixed exact-weight pool for the deterministic C05 sweep (NO random module).
_SWEEP_POOL: tuple[Fraction, ...] = (
    Fraction(1, 13),
    Fraction(1, 7),
    Fraction(2, 7),
    Fraction(1, 35),
    Fraction(3, 35),
    Fraction(1, 5),
)


def _card(symbol: str) -> Card:
    """ORACLE card constructor from a two-character canonical symbol."""
    return Card(symbol[0], symbol[1])


def _cards(*symbols: str) -> list[Card]:
    """ORACLE card list from canonical symbols."""
    return [_card(symbol) for symbol in symbols]


def _board(*symbols: str) -> tuple[Card, ...]:
    """ORACLE board tuple from canonical symbols."""
    cards = _cards(*symbols)
    assert len(cards) in (3, 4, 5)
    return tuple(cards)


def _combo(*symbols: str) -> tuple[Card, Card]:
    """ORACLE two-card combo from canonical symbols."""
    cards = _cards(*symbols)
    assert len(cards) == 2
    return (cards[0], cards[1])


def _dead(board, known=()) -> list[Card]:
    """ORACLE dead-card union: board + known_cards as one flat sequence."""
    return list(board) + list(known)


def _canonical_dead(board, known=()) -> tuple[Card, ...]:
    """ORACLE canonical DECK-order projection of the dead-card union."""
    union = set(board) | set(known)
    return tuple(card for card in DECK if card in union)


def _preconditioned_uniform(dead) -> Range:
    """ORACLE preconditioned fixture: uniform over the dead-disjoint lattice.

    ``Range.uniform(dead)`` places weight only on dead-disjoint combos
    (exactly 1/C(52-len(dead), 2) each), so ``condition(dead)`` is an
    idempotent no-op and ``total_mass()`` is exactly ``Fraction(1)``.
    """
    return Range.uniform(list(dead)).condition(list(dead))


def _oracle_weighted_facts(rng, board, dead):
    """O2 gated re-aggregation of every weighted report fact.

    Iterates the dead-disjoint live lattice ``enumerate_starting_hands(dead)``
    (never rng keys), reads the production ``classify_range(rng, board)``
    labels/booleans per combo and sums ``rng.weight(combo)`` per label/flag.
    Dead-touching and board-touching combos are therefore excluded by
    construction, mirroring the pinned ignore-dead-weights contract.
    """
    classification = classify_range(rng, board)
    value_counts = {label: Fraction(0) for label in STRENGTH_LABELS}
    pair_class_counts = {label: Fraction(0) for label in PAIR_CLASS_LABELS}
    draw_int_counts = {flag: 0 for flag in _DRAW_FLAGS}
    draw_masses = {flag: Fraction(0) for flag in _DRAW_FLAGS}
    for combo in enumerate_starting_hands(dead):
        cc = classification[combo]
        weight = rng.weight(combo)
        value_counts[cc.strength] += weight
        pair_class_counts[cc.pair_class] += weight
        for flag in _DRAW_FLAGS:
            if getattr(cc, flag):
                draw_int_counts[flag] += 1
                draw_masses[flag] += weight
    return value_counts, pair_class_counts, draw_int_counts, draw_masses


def _oracle_nut_counts(board, dead) -> tuple[int, int]:
    """O3 nut oracle: (nut_flush_draw, second_nut_flush_draw) counts.

    Fully independent of ``classes.py`` internals: pure suit arithmetic over
    public ``Card.rank``/``Card.suit`` and the dead-disjoint lattice.
    """
    board_suits = Counter(card.suit for card in board)
    nut = 0
    second = 0
    for hole in enumerate_starting_hands(dead):
        hole_suits = Counter(card.suit for card in hole)
        for suit, board_count in board_suits.items():
            if board_count + hole_suits.get(suit, 0) == 4 and len(board) < 5:
                holds_ace = any(
                    card.suit == suit and card.rank == "A" for card in hole
                )
                holds_king = any(
                    card.suit == suit and card.rank == "K" for card in hole
                )
                if holds_ace:
                    nut += 1
                if holds_king and not holds_ace:
                    second += 1
                break
    return nut, second


def _swept_range(dead, count: int, offset: int) -> Range:
    """ORACLE deterministic sparse range: fixed index-based weight vector."""
    rng = Range()
    for index, combo in enumerate(
        itertools.islice(enumerate_starting_hands(dead), count)
    ):
        rng = rng.set_weight(combo, _SWEEP_POOL[(index + offset) % len(_SWEEP_POOL)])
    return rng


# ---------------------------------------------------------------------------
# C01..C05 exact reconciliation identities (AC1)
# ---------------------------------------------------------------------------


class ReconciliationIdentityTests(unittest.TestCase):
    """sum(value_counts) == sum(pair_class_counts) == total_mass, exact."""

    maxDiff = None

    def test_c01_flop_uniform_reconciliation(self) -> None:
        """C01: flop uniform preconditioned — both identity sums equal
        total_mass() exactly; live 1176; pinned class counts/delta."""
        board = _board("Kd", "7h", "2c")
        rng = _preconditioned_uniform(board)
        self.assertEqual(Fraction(1), rng.total_mass())
        report = blocker_analysis(rng, board, ())
        self.assertEqual(
            rng.total_mass(), sum(report.value_counts.values(), Fraction(0))
        )
        self.assertEqual(
            rng.total_mass(), sum(report.pair_class_counts.values(), Fraction(0))
        )
        self.assertEqual(1176, report.live_combo_count)
        # Mechanically pinned structural decomposition (see module docstring
        # deviation notes): 150 removed combos = 9 pair + 36 suited + 105
        # offsuit.
        self.assertEqual(
            {"pair": 69, "suited": 276, "offsuit": 831}, report.class_counts
        )
        self.assertEqual(
            {"pair": 9, "suited": 36, "offsuit": 105}, report.class_removal_delta
        )
        # Documented designer-literal variant: the literal
        # ``Range.uniform(()).condition(board)`` fixture (total 196/221) must
        # satisfy the same total-relative identities.
        literal = Range.uniform(()).condition(board)
        literal_report = blocker_analysis(literal, board, ())
        self.assertEqual(
            literal.total_mass(),
            sum(literal_report.value_counts.values(), Fraction(0)),
        )
        self.assertEqual(
            literal.total_mass(),
            sum(literal_report.pair_class_counts.values(), Fraction(0)),
        )

    def test_c02_turn_uniform_reconciliation(self) -> None:
        """C02: turn uniform preconditioned on 3s 7s Ks 2h — both identity
        sums == total_mass() == Fraction(1); live C(48,2) == 1128."""
        board = _board("3s", "7s", "Ks", "2h")
        rng = _preconditioned_uniform(board)
        self.assertEqual(Fraction(1), rng.total_mass())
        report = blocker_analysis(rng, board, ())
        self.assertEqual(
            Fraction(1), sum(report.value_counts.values(), Fraction(0))
        )
        self.assertEqual(
            Fraction(1), sum(report.pair_class_counts.values(), Fraction(0))
        )
        self.assertEqual(1128, report.live_combo_count)

    def test_c03_river_uniform_reconciliation(self) -> None:
        """C03: river uniform preconditioned — both identity sums ==
        Fraction(1); flush-side draw masses 0 and both nut counts 0 (river
        gate); straight-side facts asserted by O2 oracle equality."""
        board = _board("Kd", "7h", "2c", "9s", "5d")
        rng = _preconditioned_uniform(board)
        report = blocker_analysis(rng, board, ())
        self.assertEqual(
            Fraction(1), sum(report.value_counts.values(), Fraction(0))
        )
        self.assertEqual(
            Fraction(1), sum(report.pair_class_counts.values(), Fraction(0))
        )
        self.assertEqual(Fraction(0), report.draw_masses["flush_draw"])
        self.assertEqual(Fraction(0), report.draw_masses["backdoor_flush_draw"])
        self.assertEqual(0, report.nut_availability["nut_flush_draw"])
        self.assertEqual(0, report.nut_availability["second_nut_flush_draw"])
        # Straight-side "remain live": still exactly the O2 oracle values
        # (structurally 0 at n=5 per classes.py street gates; see docstring).
        _, _, oracle_ints, oracle_masses = _oracle_weighted_facts(rng, board, board)
        self.assertEqual(oracle_ints, report.draw_int_counts)
        self.assertEqual(oracle_masses, report.draw_masses)

    def test_c04_sparse_exact_weight_reconciliation(self) -> None:
        """C04: sparse preconditioned flop rng — both identity sums ==
        total_mass() == 26/35 (non-unit); zero-filled labels stay 0."""
        board = _board("Kd", "7h", "2c")
        weights = (
            (_combo("As", "Ks"), Fraction(1, 7)),
            (_combo("Qs", "Js"), Fraction(2, 7)),
            (_combo("Ah", "Kh"), Fraction(3, 35)),
            (_combo("Td", "9d"), Fraction(1, 5)),
            (_combo("8c", "7c"), Fraction(1, 35)),
        )
        rng = Range()
        for combo, weight in weights:
            rng = rng.set_weight(combo, weight)
        expected_total = Fraction(1, 7) + Fraction(2, 7) + Fraction(3, 35) + Fraction(
            1, 5
        ) + Fraction(1, 35)
        self.assertEqual(Fraction(26, 35), expected_total)
        self.assertEqual(expected_total, rng.total_mass())
        report = blocker_analysis(rng, board, ())
        self.assertEqual(
            expected_total, sum(report.value_counts.values(), Fraction(0))
        )
        self.assertEqual(
            expected_total, sum(report.pair_class_counts.values(), Fraction(0))
        )
        self.assertEqual(set(STRENGTH_LABELS), set(report.value_counts))
        self.assertEqual(set(PAIR_CLASS_LABELS), set(report.pair_class_counts))
        # Quads/straight-flush/flush are structurally unreachable on an
        # unpaired rainbow flop, so these zero-filled labels stay Fraction(0).
        self.assertEqual(Fraction(0), report.value_counts["quads"])
        self.assertEqual(Fraction(0), report.value_counts["straight-flush"])
        self.assertEqual(Fraction(0), report.value_counts["flush"])
        # None of the five sparse combos makes two-pair or better on this
        # unpaired board, so the 'better-than-one-pair' label stays 0.
        self.assertEqual(
            Fraction(0), report.pair_class_counts["better-than-one-pair"]
        )

    def test_c05_deterministic_weight_sweep(self) -> None:
        """C05: deterministic (no random module) exact-weight sweep on turn
        and river boards — both identity sums == total_mass for every
        hand-built vector."""
        turn_dead = _dead(_board("3s", "7s", "Ks", "2h"))
        river_dead = _dead(_board("Kd", "7h", "2c", "9s", "5d"))
        checked = 0
        for dead in (turn_dead, river_dead):
            for count in (1, 5, 17, 40):
                for offset in (0, 3):
                    rng = _swept_range(dead, count, offset)
                    self.assertGreater(rng.total_mass(), Fraction(0))
                    board = tuple(dead[:4]) if dead is turn_dead else tuple(dead[:5])
                    report = blocker_analysis(rng, board, ())
                    self.assertEqual(
                        rng.total_mass(),
                        sum(report.value_counts.values(), Fraction(0)),
                    )
                    self.assertEqual(
                        rng.total_mass(),
                        sum(report.pair_class_counts.values(), Fraction(0)),
                    )
                    checked += 1
        self.assertEqual(16, checked)


# ---------------------------------------------------------------------------
# C06/C07/C30 pinned ignore-dead-weights contracts (AC2)
# ---------------------------------------------------------------------------


class IgnoreDeadWeightsTests(unittest.TestCase):
    """Dead weights ignored everywhere; preconditioning changes nothing."""

    maxDiff = None

    def test_c06_unconditioned_equals_preconditioned(self) -> None:
        """C06: blocker_analysis(rng_u, board, known) == blocker_analysis(
        rng_u.condition(dead), board, known) as full frozen-dataclass ==;
        conservation holds for the conditioned rng and is DOCUMENTED
        non-conservation (< total_mass) for the unconditioned rng."""
        board = _board("Kd", "7h", "2c")
        known = (_card("Ah"),)
        dead = _dead(board, known)
        rng_u = Range.uniform(())
        rng_c = rng_u.condition(dead)
        report_u = blocker_analysis(rng_u, board, known)
        report_c = blocker_analysis(rng_c, board, known)
        self.assertEqual(report_c, report_u)
        # Well-formed (conditioned) intake: exact conservation.
        self.assertEqual(
            rng_c.total_mass(),
            sum(report_c.value_counts.values(), Fraction(0)),
        )
        self.assertEqual(
            rng_c.total_mass(),
            sum(report_c.pair_class_counts.values(), Fraction(0)),
        )
        # Documented non-conservation for the unconditioned intake: the mass
        # sitting on dead-touching combos is ignored, so the weighted sums
        # equal exactly the dead-disjoint live mass, strictly below
        # total_mass.
        live_mass = sum(
            (
                rng_u.weight(combo)
                for combo in enumerate_starting_hands(dead)
            ),
            Fraction(0),
        )
        self.assertEqual(
            live_mass, sum(report_u.value_counts.values(), Fraction(0))
        )
        self.assertLess(live_mass, rng_u.total_mass())
        self.assertEqual(
            Fraction(1128, 1326), live_mass
        )

    def test_c07_weight_only_on_dead_combo(self) -> None:
        """C07: total mass 1/1326 carried ONLY by a dead-touching combo —
        every weighted report fact is exactly Fraction(0) (sum 0 <
        total_mass), no error; structural facts stay fully computed."""
        board = _board("Kd", "7h", "2c")
        known = (_card("Ah"),)
        dead = _dead(board, known)
        dead_combo = _combo("Ah", "Ac")
        rng = Range().set_weight(dead_combo, Fraction(1, 1326))
        self.assertEqual(Fraction(1, 1326), rng.total_mass())
        report = blocker_analysis(rng, board, known)
        self.assertEqual(
            Fraction(0), sum(report.value_counts.values(), Fraction(0))
        )
        self.assertEqual(
            Fraction(0), sum(report.pair_class_counts.values(), Fraction(0))
        )
        for flag in _DRAW_FLAGS:
            self.assertEqual(Fraction(0), report.draw_masses[flag], flag)
        # Structural facts remain weight-independent and fully computed.
        self.assertEqual(1128, report.live_combo_count)
        self.assertGreater(report.draw_int_counts["backdoor_flush_draw"], 0)
        self.assertEqual(_canonical_dead(board, known), report.dead_cards)

    def test_c30_tuple_list_intake_and_dead_weights(self) -> None:
        """C30: same rng on a tuple board vs the equivalent list board yields
        == BlockerReport; an as-given (unconditioned) rng carrying weights on
        dead combos never raises for well-formed unique inputs."""
        board = _board("Kd", "7h", "2c")
        known = (_card("Ah"),)
        rng = _preconditioned_uniform(_dead(board, known))
        report_tuple = blocker_analysis(rng, board, known)
        report_list = blocker_analysis(rng, list(board), list(known))
        self.assertEqual(report_tuple, report_list)
        # Weight on a board-touching combo (outside the lattice): no raise,
        # zero contribution.
        board_weighted = Range().set_weight(
            _combo("Kd", "7h"), Fraction(1, 2)
        )
        report_board_touching = blocker_analysis(board_weighted, board, known)
        self.assertEqual(
            Fraction(0),
            sum(report_board_touching.value_counts.values(), Fraction(0)),
        )
        # Weight on a known-card-touching combo (inside the lattice, gated
        # to 0): no raise, zero contribution.
        known_weighted = Range().set_weight(
            _combo("Ah", "Ac"), Fraction(1, 2)
        )
        report_known_touching = blocker_analysis(known_weighted, board, known)
        self.assertEqual(
            Fraction(0),
            sum(report_known_touching.value_counts.values(), Fraction(0)),
        )


# ---------------------------------------------------------------------------
# C08/C09 zero range and purity
# ---------------------------------------------------------------------------


class ZeroRangeAndPurityTests(unittest.TestCase):
    """Zero range yields all-Fraction(0) weighted facts, no error."""

    maxDiff = None

    def test_c08_zero_range(self) -> None:
        """C08: Range() — every value_counts/pair_class_counts/draw_masses
        value Fraction(0), no error; structural facts fully computed;
        rainbow flop keeps both nut counts 0."""
        board = _board("Kd", "7h", "2c")
        report = blocker_analysis(Range(), board, ())
        for label in STRENGTH_LABELS:
            self.assertEqual(Fraction(0), report.value_counts[label], label)
        for label in PAIR_CLASS_LABELS:
            self.assertEqual(
                Fraction(0), report.pair_class_counts[label], label
            )
        for flag in _DRAW_FLAGS:
            self.assertEqual(Fraction(0), report.draw_masses[flag], flag)
        self.assertEqual(1176, report.live_combo_count)
        self.assertEqual(class_counts(board), report.class_counts)
        self.assertEqual(
            {
                key: class_counts(())[key] - class_counts(board)[key]
                for key in ("pair", "suited", "offsuit")
            },
            report.class_removal_delta,
        )
        self.assertEqual(
            {"nut_flush_draw": 0, "second_nut_flush_draw": 0},
            report.nut_availability,
        )
        # Weight-independence discriminator: int draw counts stay structural
        # (backdoor flush draws exist on this board) while the masses are 0.
        self.assertGreater(report.draw_int_counts["backdoor_flush_draw"], 0)

    def test_c09_purity_and_version(self) -> None:
        """C09: rng not mutated (total_mass + 3 sampled weights unchanged);
        board tuple unchanged; two consecutive calls return == reports with
        identical repr; BLOCKERS_VERSION == '1.0.0'."""
        board = _board("Kd", "7h", "2c")
        known = (_card("Ah"),)
        dead = _dead(board, known)
        rng = _preconditioned_uniform(dead)
        mass_before = rng.total_mass()
        sampled = tuple(enumerate_starting_hands(dead))[:3]
        weights_before = tuple(rng.weight(combo) for combo in sampled)
        board_before = tuple(board)
        report1 = blocker_analysis(rng, board, known)
        report2 = blocker_analysis(rng, board, known)
        self.assertEqual(mass_before, rng.total_mass())
        self.assertEqual(
            weights_before, tuple(rng.weight(combo) for combo in sampled)
        )
        self.assertEqual(board_before, tuple(board))
        self.assertEqual(report1, report2)
        self.assertEqual(repr(report1), repr(report2))
        self.assertEqual("1.0.0", BLOCKERS_VERSION)


# ---------------------------------------------------------------------------
# C10..C15 structural facts
# ---------------------------------------------------------------------------


class StructuralFactTests(unittest.TestCase):
    """Live counts, canonical order, class counts and removal deltas."""

    maxDiff = None

    def test_c10_live_combo_counts_by_dead_size(self) -> None:
        """C10: live_combo_count == len(enumerate_starting_hands(dead)) ==
        combinations_count(52-len(dead), 2) for dead sizes 3..6."""
        fixtures = (
            (_board("Kd", "7h", "2c"), (), 1176),
            (_board("Kd", "7h", "2c"), (_card("Ah"),), 1128),
            (_board("Kd", "7h", "2c", "9s", "5d"), (), 1081),
            (_board("Kd", "7h", "2c", "9s", "5d"), (_card("Ah"),), 1035),
        )
        for board, known, expected in fixtures:
            dead = _dead(board, known)
            report = blocker_analysis(_preconditioned_uniform(dead), board, known)
            self.assertEqual(expected, report.live_combo_count)
            self.assertEqual(
                len(enumerate_starting_hands(dead)), report.live_combo_count
            )
            self.assertEqual(
                combinations_count(52 - len(dead), 2), report.live_combo_count
            )

    def test_c11_canonical_dead_order(self) -> None:
        """C11: scrambled caller input — dead_cards == tuple(c for c in DECK
        if c in union) exactly; board stored as the validated tuple."""
        board = (_card("2c"), _card("7h"), _card("Kd"))
        known = (_card("Qc"), _card("Ah"))
        rng = _preconditioned_uniform(_dead(board, known))
        report = blocker_analysis(rng, board, known)
        self.assertEqual(_canonical_dead(board, known), report.dead_cards)
        self.assertEqual(board, report.board)
        # Canonical projection is independent of caller input order.
        sorted_board = tuple(sorted(board, key=DECK.index))
        sorted_known = tuple(sorted(known, key=DECK.index))
        sorted_report = blocker_analysis(
            _preconditioned_uniform(_dead(sorted_board, sorted_known)),
            sorted_board,
            sorted_known,
        )
        self.assertEqual(report.dead_cards, sorted_report.dead_cards)

    def test_c12_class_counts_match_combinatorics(self) -> None:
        """C12: class_counts field == combinatorics.class_counts(dead) for
        flop/turn/river fixtures, over the in-test-computed dead union."""
        fixtures = (
            _board("Kd", "7h", "2c"),
            _board("3s", "7s", "Ks", "2h"),
            _board("Kd", "7h", "2c", "9s", "5d"),
        )
        for board in fixtures:
            dead = _dead(board)
            report = blocker_analysis(_preconditioned_uniform(dead), board, ())
            self.assertEqual(class_counts(dead), report.class_counts)
            self.assertEqual(
                report.live_combo_count, sum(report.class_counts.values())
            )

    def test_c13_class_removal_delta(self) -> None:
        """C13: delta == {k: full[k] - class_counts(dead)[k]} with the
        re-verified full-deck arithmetic (13*C(4,2)=78, 4*C(13,2)=312,
        1326-390=936); representative exact delta pinned on the C01 board."""
        full = class_counts(())
        self.assertEqual({"pair": 78, "suited": 312, "offsuit": 936}, full)
        self.assertEqual(78, combinations_count(4, 2) * 13)
        self.assertEqual(312, combinations_count(13, 2) * 4)
        self.assertEqual(936, combinations_count(52, 2) - 78 - 312)
        board = _board("Kd", "7h", "2c")
        report = blocker_analysis(_preconditioned_uniform(board), board, ())
        expected = {
            key: full[key] - class_counts(board)[key]
            for key in ("pair", "suited", "offsuit")
        }
        self.assertEqual(expected, report.class_removal_delta)
        # Mechanically pinned representative delta (see module docstring):
        # 150 removed combos = 9 pair + 36 suited + 105 offsuit.
        self.assertEqual(
            {"pair": 9, "suited": 36, "offsuit": 105},
            report.class_removal_delta,
        )
        self.assertEqual(150, sum(report.class_removal_delta.values()))

    def test_c14_empty_known_cards_delta(self) -> None:
        """C14: known_cards=() is legal and the delta equals
        full - class_counts(board): nonzero and a pure function of the union
        dead set (planner AC verbatim; no known-cards-only decomposition)."""
        board = _board("Kd", "7h", "2c")
        report = blocker_analysis(_preconditioned_uniform(board), board, ())
        full = class_counts(())
        self.assertEqual(
            {key: full[key] - class_counts(board)[key] for key in full},
            report.class_removal_delta,
        )
        self.assertTrue(
            all(value > 0 for value in report.class_removal_delta.values())
        )
        # Same union semantics with a known card: delta is a pure function of
        # the union dead set only.
        known = (_card("Ah"),)
        dead = _dead(board, known)
        known_report = blocker_analysis(
            _preconditioned_uniform(dead), board, known
        )
        self.assertEqual(
            {key: full[key] - class_counts(dead)[key] for key in full},
            known_report.class_removal_delta,
        )
        self.assertTrue(
            all(value > 0 for value in known_report.class_removal_delta.values())
        )

    def test_c15_river_plus_known_card(self) -> None:
        """C15: river full board (5 known) + 1 extra known card — dead size
        6, live 1035; enumerate_starting_hands(dead) accepts it."""
        board = _board("Kd", "7h", "2c", "9s", "5d")
        known = (_card("Ah"),)
        dead = _dead(board, known)
        self.assertEqual(6, len(dead))
        report = blocker_analysis(_preconditioned_uniform(dead), board, known)
        self.assertEqual(1035, report.live_combo_count)
        self.assertEqual(1035, len(enumerate_starting_hands(dead)))
        self.assertEqual(combinations_count(46, 2), report.live_combo_count)


# ---------------------------------------------------------------------------
# C16..C21 nut arithmetic, street gates and draw aggregation
# ---------------------------------------------------------------------------


class NutAndDrawTests(unittest.TestCase):
    """nut_availability, street gates, draw aggregation and overlap guard."""

    maxDiff = None

    def test_c16_turn_nut_arithmetic(self) -> None:
        """C16: turn 3s 7s Ks 2h, dead=board — nut_availability ==
        {'nut_flush_draw': 38, 'second_nut_flush_draw': 38}, matching both
        the pinned manual arithmetic and the O3 independent oracle; flush
        draw facts via O2 aggregation."""
        board = _board("3s", "7s", "Ks", "2h")
        rng = _preconditioned_uniform(board)
        report = blocker_analysis(rng, board, ())
        self.assertEqual(
            {"nut_flush_draw": 38, "second_nut_flush_draw": 0},
            report.nut_availability,
        )
        self.assertEqual((38, 0), _oracle_nut_counts(board, board))
        # Flush-draw facts via O2: 380 live combos hold exactly one spade
        # (10 live spades x 38 live non-spades); mass == count/1128 exactly
        # for the uniform fixture.
        _, _, oracle_ints, oracle_masses = _oracle_weighted_facts(rng, board, board)
        self.assertEqual(380, oracle_ints["flush_draw"])
        self.assertEqual(oracle_ints, report.draw_int_counts)
        self.assertEqual(oracle_masses, report.draw_masses)
        self.assertEqual(
            Fraction(380, 1128), report.draw_masses["flush_draw"]
        )

    def test_c17_turn_nut_ace_dead(self) -> None:
        """C17: same turn board + known_cards=(As,) — nut_flush_draw == 0
        (Ace dead); second-nut via the O3 oracle; dead size 5."""
        board = _board("3s", "7s", "Ks", "2h")
        known = (_card("As"),)
        dead = _dead(board, known)
        self.assertEqual(5, len(dead))
        rng = _preconditioned_uniform(dead)
        report = blocker_analysis(rng, board, known)
        self.assertEqual(0, report.nut_availability["nut_flush_draw"])
        self.assertEqual(
            _oracle_nut_counts(board, dead),
            (report.nut_availability["nut_flush_draw"],
             report.nut_availability["second_nut_flush_draw"]),
        )
        # Both nut booleans are 0 here: the Ace is dead (known card) and the
        # King of the 4-flush suit is on the board (see docstring deviation).
        self.assertEqual(0, report.nut_availability["second_nut_flush_draw"])

    def test_c18_rainbow_flop_nut_zero(self) -> None:
        """C18: rainbow flop — both nut counts 0 because flush_draw is
        structurally unreachable (suit sum <= 1+2 = 3), independent of rng
        weights."""
        board = _board("Kd", "7h", "2c")
        report = blocker_analysis(_preconditioned_uniform(board), board, ())
        self.assertEqual(
            {"nut_flush_draw": 0, "second_nut_flush_draw": 0},
            report.nut_availability,
        )
        self.assertEqual((0, 0), _oracle_nut_counts(board, board))

    def test_c19_river_flush_gates(self) -> None:
        """C19: river board — flush_draw/backdoor_flush_draw masses are
        Fraction(0) and both nut counts are 0 per the classes.py n<5 / n==3
        gates."""
        board = _board("Kd", "7h", "2c", "9s", "5d")
        rng = _preconditioned_uniform(board)
        report = blocker_analysis(rng, board, ())
        self.assertEqual(Fraction(0), report.draw_masses["flush_draw"])
        self.assertEqual(Fraction(0), report.draw_masses["backdoor_flush_draw"])
        self.assertEqual(0, report.draw_int_counts["flush_draw"])
        self.assertEqual(0, report.draw_int_counts["backdoor_flush_draw"])
        self.assertEqual(
            {"nut_flush_draw": 0, "second_nut_flush_draw": 0},
            report.nut_availability,
        )

    def test_c20_draw_aggregation_mixed_fixture(self) -> None:
        """C20: draw_int_counts/draw_masses over exactly the five boolean
        keys, zero-filled, == O2 in-test re-aggregation for a mixed
        flush-draw-heavy turn fixture with sparse weights; int counts equal
        the number of lattice combos with the boolean True."""
        board = _board("3s", "7s", "Ks", "2h")
        weights = (
            (_combo("As", "Ks"), Fraction(3, 7)),
            (_combo("4s", "5s"), Fraction(2, 7)),
            (_combo("Qs", "Js"), Fraction(1, 7)),
            (_combo("8d", "7d"), Fraction(1, 35)),
            (_combo("Ah", "Kh"), Fraction(1, 35)),
        )
        rng = Range()
        for combo, weight in weights:
            rng = rng.set_weight(combo, weight)
        report = blocker_analysis(rng, board, ())
        self.assertEqual(set(_DRAW_FLAGS), set(report.draw_int_counts))
        self.assertEqual(set(_DRAW_FLAGS), set(report.draw_masses))
        oracle_values, oracle_pairs, oracle_ints, oracle_masses = (
            _oracle_weighted_facts(rng, board, board)
        )
        self.assertEqual(oracle_ints, report.draw_int_counts)
        self.assertEqual(oracle_masses, report.draw_masses)
        # B6: on this dead==board fixture the family masses equal
        # classes.class_masses verbatim.
        masses = class_masses(rng, board)
        self.assertEqual(masses["strength"], report.value_counts)
        self.assertEqual(masses["pair_class"], report.pair_class_counts)
        self.assertEqual(oracle_values, report.value_counts)
        self.assertEqual(oracle_pairs, report.pair_class_counts)
        # Int counts == number of lattice combos with the boolean True,
        # recounted independently from the public classification.
        classification = classify_range(rng, board)
        lattice = enumerate_starting_hands(board)
        for flag in _DRAW_FLAGS:
            expected = sum(
                1 for combo in lattice if getattr(classification[combo], flag)
            )
            self.assertEqual(expected, report.draw_int_counts[flag], flag)

    def test_c21_overlap_not_partition(self) -> None:
        """C21: combo_draw <= min(flush_draw, straight_draw) for masses and
        int counts; sum(draw_masses.values()) is deliberately NOT asserted
        equal to total_mass (draw facts overlap, they are not a partition)."""
        board = _board("3s", "7s", "Ks", "2h")
        rng = _preconditioned_uniform(board)
        report = blocker_analysis(rng, board, ())
        combo_mass = report.draw_masses["combo_draw"]
        self.assertGreater(combo_mass, Fraction(0))
        self.assertLessEqual(
            combo_mass,
            min(
                report.draw_masses["flush_draw"],
                report.draw_masses["straight_draw"],
            ),
        )
        combo_int = report.draw_int_counts["combo_draw"]
        self.assertGreater(combo_int, 0)
        self.assertLessEqual(
            combo_int,
            min(
                report.draw_int_counts["flush_draw"],
                report.draw_int_counts["straight_draw"],
            ),
        )
        # Negative guard against a partition misreading: the draw-mass sum is
        # NOT a conservation quantity and is not compared with total_mass.


# ---------------------------------------------------------------------------
# C22/C23 determinism and frozen shape
# ---------------------------------------------------------------------------


class DeterminismAndShapeTests(unittest.TestCase):
    """Deterministic output and the pinned frozen report shape."""

    maxDiff = None

    def test_c22_determinism_unsorted_input(self) -> None:
        """C22: two calls on identical (unsorted) inputs return == reports
        with identical repr; ordering derives only from canonical DECK
        order."""
        board = (_card("2h"), _card("Ks"), _card("7s"), _card("3s"))
        known = (_card("Qd"),)
        rng = _preconditioned_uniform(_dead(board, known))
        report1 = blocker_analysis(rng, board, known)
        report2 = blocker_analysis(rng, board, known)
        self.assertEqual(report1, report2)
        self.assertEqual(repr(report1), repr(report2))
        self.assertEqual(_canonical_dead(board, known), report1.dead_cards)

    def test_c23_frozen_shape(self) -> None:
        """C23: BlockerReport is a frozen dataclass with exactly the 10
        pinned fields in order; attribute assignment raises
        FrozenInstanceError."""
        self.assertTrue(dataclasses.is_dataclass(BlockerReport))
        pinned = (
            "board",
            "dead_cards",
            "live_combo_count",
            "class_counts",
            "class_removal_delta",
            "value_counts",
            "pair_class_counts",
            "draw_int_counts",
            "draw_masses",
            "nut_availability",
        )
        self.assertEqual(
            pinned, tuple(field.name for field in dataclasses.fields(BlockerReport))
        )
        report = BlockerReport(
            board=(),
            dead_cards=(),
            live_combo_count=0,
            class_counts={},
            class_removal_delta={},
            value_counts={},
            pair_class_counts={},
            draw_int_counts={},
            draw_masses={},
            nut_availability={},
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.live_combo_count = 1
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.board = ()


# ---------------------------------------------------------------------------
# C24..C28 fail-closed matrix
# ---------------------------------------------------------------------------


class FailClosedTests(unittest.TestCase):
    """Validation fails closed: TypeError/ValueError per pinned semantics."""

    maxDiff = None

    def test_c24_duplicate_cards(self) -> None:
        """C24: duplicate card across board/known_cards -> ValueError;
        duplicate within known_cards -> ValueError."""
        board = _board("Kd", "7h", "2c")
        rng = _preconditioned_uniform(_dead(board, (_card("Ah"),)))
        with self.assertRaises(ValueError):
            blocker_analysis(rng, board, (_card("Kd"),))
        with self.assertRaises(ValueError):
            blocker_analysis(rng, board, (_card("Ah"), _card("Ah")))

    def test_c25_non_card_entries(self) -> None:
        """C25: non-Card entries fail closed — 'As' string in board ->
        TypeError; int and None in known_cards -> TypeError."""
        board = _board("Kd", "7h", "2c")
        rng = _preconditioned_uniform(board)
        with self.assertRaises(TypeError):
            blocker_analysis(rng, ("Kd", "7h", "As"), ())
        with self.assertRaises(TypeError):
            blocker_analysis(rng, board, (5,))
        with self.assertRaises(TypeError):
            blocker_analysis(rng, board, (None,))

    def test_c26_strict_range_type_identity(self) -> None:
        """C26: type(rng) is Range strict identity — Range subclass,
        object() and None all raise TypeError."""

        class _RangeSubclass(Range):
            """Minimal Range subclass for the strict-identity probe."""

        board = _board("Kd", "7h", "2c")
        with self.assertRaises(TypeError):
            blocker_analysis(_RangeSubclass(), board, ())
        with self.assertRaises(TypeError):
            blocker_analysis(object(), board, ())
        with self.assertRaises(TypeError):
            blocker_analysis(None, board, ())

    def test_c27_board_cardinality_and_sequence(self) -> None:
        """C27: board of 2 and of 6 cards -> ValueError; non-sequence board
        (int) -> TypeError."""
        rng = _preconditioned_uniform(_board("Kd", "7h", "2c"))
        with self.assertRaises(ValueError):
            blocker_analysis(rng, (_card("Kd"), _card("7h")), ())
        with self.assertRaises(ValueError):
            blocker_analysis(
                rng,
                _board("Kd", "7h", "2c", "9s", "5d") + (_card("Ah"),),
                (),
            )
        with self.assertRaises(TypeError):
            blocker_analysis(rng, 5, ())

    def test_c28_legal_sequence_intake(self) -> None:
        """C28: zero-length known_cards=() accepted on every board size
        (3, 4, 5); board given as list and as tuple both accepted."""
        fixtures = (
            (_board("Kd", "7h", "2c"), 1176),
            (_board("3s", "7s", "Ks", "2h"), 1128),
            (_board("Kd", "7h", "2c", "9s", "5d"), 1081),
        )
        for board, expected_live in fixtures:
            report = blocker_analysis(
                _preconditioned_uniform(board), board, ()
            )
            self.assertEqual(expected_live, report.live_combo_count)
        list_board = list(_board("Kd", "7h", "2c"))
        report_list = blocker_analysis(
            _preconditioned_uniform(list_board), list_board, ()
        )
        self.assertEqual(tuple(list_board), report_list.board)
        self.assertEqual(1176, report_list.live_combo_count)


# ---------------------------------------------------------------------------
# C29 RED evidence discipline
# ---------------------------------------------------------------------------


class RedEvidenceTests(unittest.TestCase):
    """C29: RED discipline artifacts exist in the canonical run directory.

    The RED protocol itself is evidence captured OUTSIDE this suite:

    - RED-1: with ``src/poker/range/blockers.py`` absent,
      ``python -m unittest tests.range.test_blockers`` exits 1 with zero
      tests collected (import error) while bare ``python -m unittest
      discover`` still collects the suite through the existing
      ``tests/range`` package (``__init__.py`` exists, asserted below).
    - RED-2: against the skeleton (``BLOCKERS_VERSION`` + frozen
      ``BlockerReport`` + ``blocker_analysis`` raising
      ``NotImplementedError('POKER-RANGE-BLOCKERS-001 RED skeleton')``),
      every functional case fails with NotImplementedError and every
      negative/validation case fails on the missing fail-closed behavior;
      only C23-style surface checks pass early.

    Both stages are captured verbatim under
    ``.harness/runs/POKER-RANGE-BLOCKERS-001/red-evidence/``; this test
    pins that the captured artifacts exist and that the package is bare-
    discoverable.
    """

    maxDiff = None

    def test_c29_red_evidence_captured(self) -> None:
        """C29: tests/range package is discover-collectible and the RED
        sieve artifact exists in the canonical run directory."""
        package_init = Path(__file__).resolve().parent / "__init__.py"
        self.assertTrue(
            package_init.is_file(),
            "tests/range/__init__.py must exist for bare discover collection",
        )
        artifact = None
        for root in Path(__file__).resolve().parents[2:5]:
            candidate = (
                root
                / ".harness"
                / "runs"
                / "POKER-RANGE-BLOCKERS-001"
                / "red-evidence"
                / "red-sieve-output.txt"
            )
            if candidate.is_file():
                artifact = candidate
                break
        self.assertIsNotNone(
            artifact,
            "RED sieve evidence red-evidence/red-sieve-output.txt must be "
            "captured in the canonical run directory",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
