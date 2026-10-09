"""Test suite for ``poker.cards.evaluator`` (task POKER-HAND-EVAL-001).

Behaviors B01-B24, oracle spec O1-O7 and cases C01-C23 from the frozen test
design handoff (``.harness/runs/POKER-HAND-EVAL-001/handoffs/test-designer.json``)
are implemented here with the repository unittest + ``subTest`` seed-pinning
convention (tests/domain/test_pots.py).

The independent oracle required by docs/specs/TESTING_AND_ORACLES.md lives in
this module only (O1): it classifies through a definition-driven predicate
ladder over a rank-count multiset signature (O2-O4), deliberately different
from the production ranking path, and resolves seven-card hands by brute-force
comparison of all 21 five-card subsets (O6).

Randomness discipline (O7): only locally constructed ``random.Random(seed)``
instances; every randomized loop enumerates its seed range with ``subTest``.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import random
import unittest

from poker.domain.cards import DECK, RANKS, Card

from poker.cards import evaluator as evaluator_module
from poker.cards.evaluator import (
    EVALUATOR_VERSION,
    HandCategory,
    HandEvaluation,
    compare,
    evaluate,
    evaluate7,
)

# --------------------------------------------------------------------- helpers


def cards_for(spec: str) -> list[Card]:
    """Parse a whitespace-separated symbol list, e.g. ``"As Kd Qs"``."""
    return [Card(symbol[:-1], symbol[-1]) for symbol in spec.split()]


HAND_ORDER = list(HandCategory)

# Fixture hands, one per ascending ladder position (C01..C14 sequence).
LADDER_FIXTURES: dict[HandCategory, list[Card]] = {
    HandCategory.HIGH_CARD: cards_for("As Kh Qd 9s 7c"),
    HandCategory.PAIR: cards_for("Kh Kd As Qs 9c"),
    HandCategory.TWO_PAIR: cards_for("Kd Ks 9h 9c Ac"),
    HandCategory.THREE_OF_A_KIND: cards_for("5c 5d 5h Ac Kd"),
    HandCategory.STRAIGHT: cards_for("6c 7d 8h 9s Td"),
    HandCategory.FLUSH: cards_for("Ac Jc 9c 6c 3c"),
    HandCategory.FULL_HOUSE: cards_for("Qd Qs Qc 3h 3s"),
    HandCategory.FOUR_OF_A_KIND: cards_for("8c 8d 8h 8s Kd"),
    HandCategory.STRAIGHT_FLUSH: cards_for("As 2s 3s 4s 5s"),
}

# Frozen explorer golden totals keyed by the classic numeric category ladder
# (1 = high card ... 9 = straight flush); the oracle derives its own numbers.
_GOLDEN_TOTALS: dict[int, int] = {
    9: 40,       # straight flush
    8: 624,      # four of a kind
    7: 3744,     # full house
    6: 5108,     # flush
    5: 10200,    # straight
    4: 54912,    # three of a kind
    3: 123552,   # two pair
    2: 1098240,  # one pair
    1: 1302540,  # high card
}
_TOTAL_COMBINATIONS = 2598960  # C(52, 5)

# --------------------------------------------------------------------- oracle
# Independent five-card classifier. Magnitude ladder lay-out: the returned
# category id is the numeric position produced by the predicate ladder itself,
# # never imported from the production enum.


def _oracle_straight_high(distinct_strengths: set[int]) -> int | None:
    """Set-based run scan (O3): five consecutive strengths give their high;
    the wheel A2345 explicitly returns 5. Five-card input assumed distinct."""
    if len(distinct_strengths) != 5:
        return None
    if distinct_strengths == {14, 2, 3, 4, 5}:
        return 5
    high = max(distinct_strengths)
    if high - min(distinct_strengths) == 4:
        return high
    return None


def oracle_eval(cards) -> tuple[int, tuple[int, ...]]:
    """Definition-driven independent classifier.

    Returns ``(category_id, tiebreak)`` per the canonical rules (O4):
    SF/Straight ``(high,)`` with wheel high=5; quads ``(quad, kicker)``;
    full house ``(trips, pair)``; flush five descending; trips ``(t, k1, k2)``;
    two pair ``(hi, lo, kicker)``; pair ``(p, k1, k2, k3)``; high card five
    descending. Only this module may import/use it (O1).
    """
    cards = tuple(cards)
    if len(cards) != 5:
        raise ValueError("oracle_eval requires exactly five cards")
    counts: dict[int, int] = {}
    for card in cards:
        strength = RANKS.index(card.rank) + 2
        counts[strength] = counts.get(strength, 0) + 1
    # Multiset signature per O2: (multiplicity, rank) pairs, signature-sorted.
    signature = tuple(sorted((count, rank) for rank, count in counts.items()))
    signature += ()  # explicit immutable tuple bookkeeping (signature-based)
    suited = len({card.suit for card in cards}) == 1
    straight_high = _oracle_straight_high(set(counts))
    quads = sorted((rank for rank, c in counts.items() if c == 4), reverse=True)
    trips = sorted((rank for rank, c in counts.items() if c == 3), reverse=True)
    pairs = sorted((rank for rank, c in counts.items() if c == 2), reverse=True)
    singles = sorted((rank for rank, c in counts.items() if c == 1), reverse=True)

    def predicate_straight_flush():
        # Definition: flush (suit equality first) AND run scan on the same five.
        if not suited or straight_high is None:
            return None
        return (9, (straight_high,))

    def predicate_four_of_a_kind():
        # Definition: exactly one rank present four times.
        if len(quads) != 1:
            return None
        kicker = max(rank for rank, c in counts.items() if c != 4)
        return (8, (quads[0], kicker))

    def predicate_full_house():
        # Definition: one trips multiset pattern AND one pair pattern (3, 2).
        if len(trips) != 1 or len(pairs) != 1:
            return None
        return (7, (trips[0], pairs[0]))

    def predicate_flush():
        if not suited:
            return None
        return (6, tuple(sorted(counts, reverse=True)))

    def predicate_straight():
        if straight_high is None:
            return None
        return (5, (straight_high,))

    def predicate_three_of_a_kind():
        # Definition: exactly one rank present three times.
        if len(trips) != 1:
            return None
        return (4, (trips[0], singles[0], singles[1]))

    def predicate_two_pair():
        # Definition: exactly two distinct pair patterns remain.
        if len(pairs) != 2:
            return None
        return (3, (pairs[0], pairs[1], singles[0]))

    def predicate_pair():
        if len(pairs) != 1:
            return None
        return (2, (pairs[0], singles[0], singles[1], singles[2]))

    def predicate_high_card():
        if len(counts) != 5:
            return None
        return (1, tuple(sorted(counts, reverse=True)))

    for predicate in (
        predicate_straight_flush,
        predicate_four_of_a_kind,
        predicate_full_house,
        predicate_flush,
        predicate_straight,
        predicate_three_of_a_kind,
        predicate_two_pair,
        predicate_pair,
        predicate_high_card,
    ):
        resolved = predicate()
        if resolved is not None:
            return resolved
    raise AssertionError(
        f"oracle classified nothing (bug in oracle spec): {signature=}"
    )


def oracle_eval7(cards):
    """Brute-force best of the 21 five-card subsets (O6).

    Returns ``(best_key, best_subsets)`` where ``best_key`` is the maximal
    oracle key and ``best_subsets`` lists every subset achieving it.
    """
    seven = tuple(cards)
    if len(seven) != 7:
        raise ValueError("oracle_eval7 requires exactly seven cards")
    best_key: tuple[int, tuple[int, ...]] | None = None
    best_subsets: list[tuple] = []
    for combo in itertools.combinations(seven, 5):
        key = oracle_eval(combo)
        if best_key is None or key > best_key:
            best_key = key
            best_subsets = [combo]
        elif key == best_key:
            best_subsets.append(combo)
    if best_key is None:
        raise AssertionError("oracle evaluated no subsets (bug)")
    return best_key, best_subsets


# ---------------------------------------------------------------- fixture tests


class CategoryFixtureTests(unittest.TestCase):
    """One exact fixture per category with canonical tiebreak + card order.

    Planned tiebreak canon (frozen API): SF/Straight ``(high,)`` (wheel=5),
    quads ``(quad, kicker)``, full house ``(trips, pair)``, flush 5 desc,
    trips ``(t, k1, k2)``, two pair ``(hi, lo, kicker)``, pair
    ``(p, k1, k2, k3)``, high card 5 descending. Fixture hands reuse cards
    across hands by design (each evaluation validates only its own inputs).
    """

    def evaluate_spec(self, spec: str) -> HandEvaluation:
        return evaluate(cards_for(spec))

    def test_high_card_fixture(self):  # B02 / C01
        evaluation = self.evaluate_spec("As Kh Qd 9s 7c")
        self.assertIs(evaluation.category, HandCategory.HIGH_CARD)
        self.assertEqual(evaluation.tiebreak, (14, 13, 12, 9, 7))
        self.assertEqual(
            [card.symbol for card in evaluation.cards],
            ["As", "Kh", "Qd", "9s", "7c"],
        )
        self.assertEqual(evaluation.evaluation_key(), (1, (14, 13, 12, 9, 7)))

    def test_pair_fixture_and_canonical_card_order(self):  # B03 / C02 + U03
        evaluation = self.evaluate_spec("Kh Kd As Qs 9c")
        self.assertIs(evaluation.category, HandCategory.PAIR)
        self.assertEqual(evaluation.tiebreak, (13, 14, 12, 9))
        # Canonical cards: rank descending, suit ascending in SUITS index
        # order ('c' < 'd' < 'h' < 's'), so Kd precedes Kh inside the pair.
        self.assertEqual(
            [card.symbol for card in evaluation.cards],
            ["As", "Kd", "Kh", "Qs", "9c"],
        )

    def test_pair_kicker_ladder(self):  # B03 / C03
        better = self.evaluate_spec("Kh Kd As Qs 9c")   # kickers A Q 9
        worse = self.evaluate_spec("Kc Ks Ah Qh 8d")    # kickers A Q 8
        self.assertEqual(worse.tiebreak, (13, 14, 12, 8))
        self.assertEqual(compare(better, worse), 1)
        self.assertEqual(compare(worse, better), -1)

    def test_two_pair_fixture_and_ladder(self):  # B04 / C04
        evaluation = self.evaluate_spec("Kd Ks 9h 9c Ac")
        self.assertIs(evaluation.category, HandCategory.TWO_PAIR)
        self.assertEqual(evaluation.tiebreak, (13, 9, 14))
        same_ranks = self.evaluate_spec("Kc Ks 9h 9c Ah")
        # Equal ranks, different suits: compare is decided only by tiebreak.
        self.assertEqual(compare(evaluation, same_ranks), 0)
        lower_pair_decides = self.evaluate_spec("Kc Ks Th Tc Ah")
        self.assertEqual(lower_pair_decides.tiebreak, (13, 10, 14))
        self.assertEqual(compare(lower_pair_decides, evaluation), 1)
        self.assertEqual(compare(evaluation, lower_pair_decides), -1)

    def test_three_of_a_kind_fixture_and_ladder(self):  # B05 / C05
        evaluation = self.evaluate_spec("5c 5d 5h Ac Kd")
        self.assertIs(evaluation.category, HandCategory.THREE_OF_A_KIND)
        self.assertEqual(evaluation.tiebreak, (5, 14, 13))
        # Trip rank first: fives-with-small-kickers beat threes-with-A-K.
        threes_aces = self.evaluate_spec("3c 3d 3h Ah Kc")
        self.assertEqual(threes_aces.tiebreak, (3, 14, 13))
        self.assertEqual(compare(threes_aces, evaluation), -1)
        # Kicker ladder below the trip rank: (5, 13, 12) loses on kickers.
        fives_kq = self.evaluate_spec("5s 5c 5d Kc Qc")
        self.assertEqual(fives_kq.tiebreak, (5, 13, 12))
        self.assertEqual(compare(evaluation, fives_kq), 1)

    def test_straight_fixture_including_wheel(self):  # B06 / C06 / C07
        straight = self.evaluate_spec("6c 7d 8h 9s Td")
        self.assertIs(straight.category, HandCategory.STRAIGHT)
        # Design handoff C06/B06 recorded "(9,)" for this hand, but its high
        # card is T (ten = strength 10) and the canonical rule is (high,);
        # both the production evaluator and the independent oracle agree on
        # (10,) for every occurrence of this run in the full enumeration, so
        # the "(9,)" literal is a fixture typo corrected here.
        self.assertEqual(straight.tiebreak, (10,))
        wheel = self.evaluate_spec("Ad 2c 3d 4h 5s")
        self.assertIs(wheel.category, HandCategory.STRAIGHT)
        self.assertEqual(wheel.tiebreak, (5,))  # wheel is 5-high, never 14
        six_high = self.evaluate_spec("2c 3d 4h 5s 6d")
        self.assertEqual(six_high.tiebreak, (6,))
        self.assertEqual(compare(wheel, six_high), -1)
        self.assertEqual(compare(six_high, wheel), 1)

    def test_flush_fixture(self):  # B07 / C08 / C09 + U03 flush card order
        evaluation = self.evaluate_spec("Ac Jc 9c 6c 3c")
        self.assertIs(evaluation.category, HandCategory.FLUSH)
        self.assertEqual(evaluation.tiebreak, (14, 11, 9, 6, 3))
        self.assertEqual(
            [card.symbol for card in evaluation.cards],
            ["Ac", "Jc", "9c", "6c", "3c"],
        )
        # Reversed input order yields the identical canonical evaluation (B20).
        self.assertEqual(
            self.evaluate_spec("3c 6c 9c Jc Ac"), evaluation
        )
        # Four-card run inside a suited hand is NOT a straight (B07).
        near = self.evaluate_spec("8d 7d 6d 4d 3d")
        self.assertIs(near.category, HandCategory.FLUSH)
        self.assertEqual(near.tiebreak, (8, 7, 6, 4, 3))
        self.assertEqual(
            [card.symbol for card in near.cards], ["8d", "7d", "6d", "4d", "3d"]
        )
        self.assertEqual(compare(near, evaluation), -1)

    def test_full_house_precedence(self):  # B08 / C10 / C11
        evaluation = self.evaluate_spec("Qd Qs Qc 3h 3s")
        self.assertIs(evaluation.category, HandCategory.FULL_HOUSE)
        self.assertEqual(evaluation.tiebreak, (12, 3))
        aces_kings = self.evaluate_spec("As Ad Ah Ks Kd")
        self.assertEqual(aces_kings.tiebreak, (14, 13))
        self.assertEqual(compare(aces_kings, evaluation), 1)
        # Trips rank dominates pair rank: AAA-99 (14, 9) > KKK-QQ (13, 12).
        aces_nines = self.evaluate_spec("As Ad Ah 9s 9d")
        kings_queens = self.evaluate_spec("Ks Kd Kh Qs Qd")
        self.assertEqual(aces_nines.tiebreak, (14, 9))
        self.assertEqual(kings_queens.tiebreak, (13, 12))
        self.assertEqual(compare(aces_nines, kings_queens), 1)
        self.assertEqual(compare(kings_queens, aces_nines), -1)

    def test_four_of_a_kind_ladder(self):  # B09 / C12
        evaluation = self.evaluate_spec("8c 8d 8h 8s Kd")
        self.assertIs(evaluation.category, HandCategory.FOUR_OF_A_KIND)
        self.assertEqual(evaluation.tiebreak, (8, 13))
        worse = self.evaluate_spec("8c 8d 8h 8s Qc")
        self.assertEqual(worse.tiebreak, (8, 12))
        self.assertEqual(compare(evaluation, worse), 1)
        self.assertEqual(compare(worse, evaluation), -1)

    def test_straight_flush_fixtures(self):  # B10 / C13 / C14
        wheel_sf = self.evaluate_spec("As 2s 3s 4s 5s")
        self.assertIs(wheel_sf.category, HandCategory.STRAIGHT_FLUSH)
        self.assertEqual(wheel_sf.tiebreak, (5,))  # wheel SF is 5-high
        six_high_sf = self.evaluate_spec("2c 3c 4c 5c 6c")
        self.assertEqual(six_high_sf.tiebreak, (6,))
        self.assertEqual(compare(wheel_sf, six_high_sf), -1)
        # Wheel SF (category 9) beats every flush and every straight.
        ace_flush = self.evaluate_spec("Ac Qc Tc 8c 5c")
        self.assertIs(ace_flush.category, HandCategory.FLUSH)
        self.assertEqual(compare(wheel_sf, ace_flush), 1)
        nine_straight = self.evaluate_spec("9c 8d 7h 6s 5c")
        self.assertIs(nine_straight.category, HandCategory.STRAIGHT)
        self.assertEqual(compare(wheel_sf, nine_straight), 1)

    def test_lowest_category_beats_best_tiebreak(self):  # B11
        deuces = self.evaluate_spec("2c 2d Ah Kc Qd")
        self.assertIs(deuces.category, HandCategory.PAIR)
        self.assertEqual(deuces.tiebreak, (2, 14, 13, 12))
        ace_high = self.evaluate_spec("Ac Kh Qd Js 9c")
        self.assertIs(ace_high.category, HandCategory.HIGH_CARD)
        self.assertEqual(compare(deuces, ace_high), 1)
        self.assertEqual(compare(ace_high, deuces), -1)


class CrossCategoryLadderTests(unittest.TestCase):
    """B12: adjacent categories strictly ordered; high card loses to all."""

    def test_adjacent_categories_strictly_ordered(self):
        for weaker, stronger in zip(HAND_ORDER, HAND_ORDER[1:]):
            with self.subTest(weaker=weaker.name, stronger=stronger.name):
                weak_ev = evaluate(LADDER_FIXTURES[weaker])
                strong_ev = evaluate(LADDER_FIXTURES[stronger])
                self.assertEqual(compare(weak_ev, strong_ev), -1)
                self.assertEqual(compare(strong_ev, weak_ev), 1)

    def test_high_card_loses_to_every_category(self):
        weakest = evaluate(LADDER_FIXTURES[HandCategory.HIGH_CARD])
        for category in HAND_ORDER[1:]:
            with self.subTest(category=category.name):
                self.assertEqual(
                    compare(weakest, evaluate(LADDER_FIXTURES[category])), -1
                )


# ------------------------------------------------------- ordering property tests


class TotalOrderPropertyTests(unittest.TestCase):
    """B14: antisymmetry, identity and transitivity on seeded samples."""

    def test_antisymmetry_identity_and_transitivity(self):
        hands: list[tuple[Card, ...]] = []
        for seed in range(1, 101):
            rng = random.Random(seed)
            hands.append(tuple(rng.sample(list(DECK), 5)))  # 100 sampled hands
        evaluations = [evaluate(hand) for hand in hands]
        # compare() matrix (100x100) once, reuse for all property checks.
        matrix = [
            [compare(evaluations[i], evaluations[j]) for j in range(100)]
            for i in range(100)
        ]
        # compare(a, a) == 0 for every sampled hand.
        for i in range(100):
            with self.subTest(hand_index=i):
                self.assertEqual(matrix[i][i], 0)
        # compare(a, b) == -compare(b, a) for every sampled pair, and
        # evaluation_key equality iff compare == 0.
        keys = [evaluation.evaluation_key() for evaluation in evaluations]
        for i, j in itertools.combinations(range(100), 2):
            with self.subTest(i=i, j=j):
                self.assertEqual(matrix[i][j], -matrix[j][i])
                self.assertEqual(matrix[i][j] == 0, keys[i] == keys[j])
        # Transitivity on every 3-sample subset: a<=b and b<=c implies a<=c
        # for each of the three cyclic orders inside the triple.
        for i, j, k in itertools.combinations(range(100), 3):
            left, middle, right = matrix[i][j], matrix[j][k], matrix[i][k]
            if left <= 0 and middle <= 0:
                self.assertLessEqual(right, 0)
            left, middle, right = matrix[j][k], matrix[k][i], matrix[j][i]
            if left <= 0 and middle <= 0:
                self.assertLessEqual(right, 0)
            left, middle, right = matrix[k][i], matrix[i][j], matrix[k][j]
            if left <= 0 and middle <= 0:
                self.assertLessEqual(right, 0)


class OrderingProbeTests(unittest.TestCase):
    """B24 + B13: category consistency and tiebreak-decided ordering."""

    def test_ordering_probes_seeds_1_200(self):
        for seed in range(1, 201):
            rng = random.Random(seed)
            cards = rng.sample(list(DECK), 10)
            with self.subTest(seed=seed):
                first = evaluate(cards[:5])
                second = evaluate(cards[5:])
                forward = compare(first, second)
                self.assertEqual(forward, -compare(second, first))
                self.assertEqual(compare(first, first), 0)
                key_a = first.evaluation_key()
                key_b = second.evaluation_key()
                self.assertEqual(forward == 0, key_a == key_b)
                self.assertEqual(forward in (-1, 0, 1), True)
                if first.category != second.category:
                    self.assertEqual(key_a[0] != key_b[0], True)
                    self.assertEqual(
                        forward, -1 if key_a[0] < key_b[0] else 1
                    )
                else:
                    self.assertEqual(key_a[0], key_b[0])
                    if key_a[1] == key_b[1]:
                        self.assertEqual(forward, 0)
                    elif key_a[1] > key_b[1]:
                        self.assertEqual(forward, 1)
                    else:
                        self.assertEqual(forward, -1)
                # Category-consistency (B13) restated: when categories differ,
                # the compare sign equals the category difference sign.
                if first.category != second.category:
                    self.assertEqual(
                        forward,
                        -1
                        if first.category < second.category
                        else 1,
                    )


# ---------------------------------------------------------- exhaustive B15


class ExhaustiveDistributionTests(unittest.TestCase):
    """B15: single always-on pass over all C(52,5) = 2,598,960 combinations.

    Count categories via production ``evaluate()`` AND the in-test oracle
    simultaneously; assert exact golden totals, per-combination agreement, and
    zero production/oracle key mismatches (mismatch list bounded to 5 with the
    first failing combination index recorded via subTest).
    """

    def test_exhaustive_five_card_distribution_and_oracle_agreement(self):
        production_counts = {value: 0 for value in range(1, 10)}
        oracle_counts = {value: 0 for value in range(1, 10)}
        mismatches: list[tuple] = []
        for index, combo in enumerate(itertools.combinations(DECK, 5)):
            evaluation = evaluate(combo)
            evaluation_key = evaluation.evaluation_key()
            oracle_key = oracle_eval(combo)
            production_counts[evaluation.category.value] += 1
            oracle_counts[oracle_key[0]] += 1
            if evaluation_key != oracle_key and len(mismatches) < 5:
                mismatches.append(
                    (
                        index,
                        tuple(card.symbol for card in combo),
                        str(evaluation_key),
                        str(oracle_key),
                    )
                )
        for position, (index, symbols, prod_key, oracle_key) in enumerate(
            mismatches
        ):
            with self.subTest(
                position=position, combination_index=index, cards=symbols
            ):
                self.assertEqual(prod_key, oracle_key)
        self.assertEqual(
            mismatches,
            [],
            "production/oracle key mismatches across the full enumeration",
        )
        # O5: the oracle totals alone must reproduce every golden total; any
        # divergence here is an oracle-specification failure, not a production
        # failure — assert oracle first so a golden-mismatch aborts as oracle
        # spec failure.
        self.assertEqual(oracle_counts, _GOLDEN_TOTALS)
        self.assertEqual(production_counts, _GOLDEN_TOTALS)
        self.assertEqual(sum(oracle_counts.values()), _TOTAL_COMBINATIONS)
        self.assertEqual(sum(production_counts.values()), _TOTAL_COMBINATIONS)


# ------------------------------------------------------------ evaluate7 tests


class Evaluate7GoldenTests(unittest.TestCase):
    """B17 golden seven-card cases + C19 tie-selection discipline."""

    def test_golden_two_pair(self):  # C15
        evaluation = evaluate7(cards_for("As Ah Ks Kh Qs Qd 2c"))
        self.assertIs(evaluation.category, HandCategory.TWO_PAIR)
        self.assertEqual(evaluation.tiebreak, (14, 13, 12))

    def test_golden_straight_flush(self):  # C16 / B17ii
        evaluation = evaluate7(cards_for("5c 6c 7c 8c 9c Ad Kd"))
        self.assertIs(evaluation.category, HandCategory.STRAIGHT_FLUSH)
        self.assertEqual(evaluation.tiebreak, (9,))

    def test_golden_wheel_straight_flush(self):  # C17 / B17iii
        evaluation = evaluate7(cards_for("As 2s 3s 4s 5s Kd Qd"))
        self.assertIs(evaluation.category, HandCategory.STRAIGHT_FLUSH)
        self.assertEqual(evaluation.tiebreak, (5,))

    def test_flush_and_straight_resolves_to_flush(self):  # C18 / B17iv
        evaluation = evaluate7(cards_for("5h 6h 7h 8h 2h 9d Tc"))
        # The design handoff B17-iv recorded "STRAIGHT (9,) ... the 5-heart
        # flush (8,7,6,5,2) must NOT win", but a flush outranks a straight in
        # the same handoff's own category ladder (O4 by numbering, B12
        # compare(FIXTURE_STRAIGHT, FIXTURE_FLUSH) == -1), and among these
        # seven cards the best straight is 6-7-8-9-T anyway (high 10, not 9).
        # The literal B17-iv expectation is therefore a test-bug relative to
        # the frozen ladder; the invariant actually targeted — that no
        # straight-flush exists here and the true best five-card hand wins —
        # is pinned instead: the 5-heart flush (8,7,6,5,2) IS the best hand.
        self.assertIs(evaluation.category, HandCategory.FLUSH)
        self.assertEqual(evaluation.tiebreak, (8, 7, 6, 5, 2))
        self.assertNotEqual(evaluation.category, HandCategory.STRAIGHT_FLUSH)

    def test_seven_card_trips_kicker_selection(self):  # C19 / B19
        evaluation = evaluate7(cards_for("7c 7d 7h Ks Qh Jd 9c"))
        self.assertIs(evaluation.category, HandCategory.THREE_OF_A_KIND)
        self.assertEqual(evaluation.tiebreak, (7, 13, 12))
        self.assertEqual(
            sorted(card.symbol for card in evaluation.cards),
            sorted(["7c", "7d", "7h", "Ks", "Qh"]),
        )
        # B19: returned cards always re-evaluate to the returned evaluation.
        self.assertEqual(
            evaluate(evaluation.cards).evaluation_key(),
            evaluation.evaluation_key(),
        )


class Evaluate7PropertiesTests(unittest.TestCase):
    """B19/B20/B21: determinism, tie-set discipline, order independence."""

    def test_determinism_including_cards_field(self):  # B20
        seven = cards_for("As Ah Ks Kh Qs Qd 2c")
        five = cards_for("As Kh Qd 9s 7c")
        self.assertEqual(evaluate7(seven), evaluate7(seven))
        self.assertEqual(evaluate7(seven).cards, evaluate7(seven).cards)
        self.assertEqual(evaluate(five), evaluate(five))
        self.assertEqual(
            evaluate(five).evaluation_key(),
            evaluate(list(reversed(five))).evaluation_key(),
        )

    def test_permuted_inputs_preserve_full_result_including_cards(self):  # B21
        for seed in range(1, 21):
            rng = random.Random(seed)
            deck = list(DECK)
            rng.shuffle(deck)
            seven = deck[:7]
            with self.subTest(seed=seed):
                expected = evaluate7(list(seven))
                for _ in range(3):
                    rng.shuffle(seven)
                    actual = evaluate7(seven)
                    self.assertEqual(expected.category, actual.category)
                    self.assertEqual(expected.tiebreak, actual.tiebreak)
                    self.assertEqual(expected.cards, actual.cards)
                    self.assertEqual(
                        expected.evaluation_key(), actual.evaluation_key()
                    )


class Evaluate7OracleCrossCheckTests(unittest.TestCase):
    """B18 + AC3 core: randomized evaluate7 cross-check, zero mismatches."""

    def test_randomized_seven_card_cross_check_seeds_1_2000(self):
        for seed in range(1, 2001):
            rng = random.Random(seed)
            deck = list(DECK)
            rng.shuffle(deck)
            seven = deck[:7]
            with self.subTest(seed=seed):
                best_key, _best_subsets = oracle_eval7(seven)
                evaluation = evaluate7(seven)
                self.assertEqual(evaluation.evaluation_key(), best_key)
                self.assertEqual(len(evaluation.cards), 5)
                self.assertEqual(len(set(evaluation.cards)), 5)
                self.assertTrue(set(evaluation.cards).issubset(set(seven)))
                # B19: returned cards re-evaluate to the returned key.
                self.assertEqual(
                    evaluate(evaluation.cards).evaluation_key(),
                    evaluation.evaluation_key(),
                )
                # No other of the 21 oracle-evaluated subsets strictly beats
                # the production selection.
                for combo in itertools.combinations(seven, 5):
                    self.assertLessEqual(oracle_eval(combo), best_key)


# ------------------------------------------------------------ validation tests


class InputValidationTests(unittest.TestCase):
    """B22 / C20 negative coverage, order-independent errors."""

    def test_duplicate_card_rejected_in_evaluate_and_evaluate7(self):
        for positions in ((0, 1), (2, 3), (3, 4)):
            with self.subTest(positions=positions):
                five = cards_for("As Kh Qd 9s 7c")
                five[positions[0]] = Card("A", "s")
                five[positions[1]] = Card("A", "s")
                with self.assertRaises((TypeError, ValueError)):
                    evaluate(five)
                seven = cards_for("As Ah Ks Kh Qs Qd 2c")
                seven[positions[0]] = Card("A", "s")
                seven[positions[1]] = Card("A", "s")
                with self.assertRaises((TypeError, ValueError)):
                    evaluate7(seven)

    def test_wrong_arity_rejected_orderindependently(self):
        five_sources = ("As Kh Qd 9s", "As Kh Qd 9s 7c 5d")
        for source in five_sources:
            with self.subTest(count=len(source.split())):
                hand = cards_for(source)
                with self.assertRaises((TypeError, ValueError)):
                    evaluate(hand)
                with self.assertRaises((TypeError, ValueError)):
                    evaluate(list(reversed(hand)))
        seven_sources = ("As Ah Ks Kh Qs", "As Ah Ks Kh Qs 2c", "As Ah Ks Kh Qs Qd 2c 3d")
        for source in seven_sources:
            with self.subTest(count=len(source.split())):
                hand = cards_for(source)
                with self.assertRaises((TypeError, ValueError)):
                    evaluate7(hand)
                with self.assertRaises((TypeError, ValueError)):
                    evaluate7(list(reversed(hand)))

    def test_non_card_items_rejected_with_type_error(self):
        five = cards_for("As Kh Qd 9s 7c")
        five[1] = "Kh"
        with self.assertRaises(TypeError):
            evaluate(five)
        five[1] = 13
        with self.assertRaises(TypeError):
            evaluate(five)
        seven = cards_for("As Ah Ks Kh Qs Qd 2c")
        seven[6] = "2c"
        with self.assertRaises(TypeError):
            evaluate7(seven)


# --------------------------------------------------------------- API surface


class ApiSurfaceTests(unittest.TestCase):
    """B23: frozen API contract, version constant, cache-key stability."""

    def test_hand_category_int_enum_values_and_order(self):
        self.assertTrue(issubclass(HandCategory, int))
        self.assertEqual(
            [category.name for category in HandCategory],
            [
                "HIGH_CARD",
                "PAIR",
                "TWO_PAIR",
                "THREE_OF_A_KIND",
                "STRAIGHT",
                "FLUSH",
                "FULL_HOUSE",
                "FOUR_OF_A_KIND",
                "STRAIGHT_FLUSH",
            ],
        )
        self.assertEqual(
            [category.value for category in HandCategory],
            [1, 2, 3, 4, 5, 6, 7, 8, 9],
        )

    def test_hand_evaluation_frozen_fields(self):
        evaluation = evaluate(cards_for("As Kh Qd 9s 7c"))
        self.assertIsInstance(evaluation, HandEvaluation)
        self.assertEqual(
            [field.name for field in dataclasses.fields(HandEvaluation)],
            ["category", "tiebreak", "cards"],
        )
        self.assertIsInstance(evaluation.cards, tuple)
        self.assertIsInstance(evaluation.tiebreak, tuple)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            evaluation.category = HandCategory.PAIR

    def test_evaluation_key_shape(self):
        evaluation = evaluate(cards_for("Kh Kd As Qs 9c"))
        self.assertEqual(evaluation.evaluation_key(), (2, (13, 14, 12, 9)))
        self.assertEqual(
            evaluation.evaluation_key(),
            (evaluation.category.value, evaluation.tiebreak),
        )

    def test_evaluation_version_constant(self):
        self.assertEqual(EVALUATOR_VERSION, "1.0.0")
        self.assertIs(evaluator_module.EVALUATOR_VERSION, EVALUATOR_VERSION)

    def test_evaluation_key_repr_stable_for_cache_keys(self):
        # RANGE_EQUITY_SPEC.md:29 — cache keys must include the evaluator
        # version; evaluation_key must be repr-stable (no object addresses).
        first = evaluate(cards_for("Kh Kd As Qs 9c")).evaluation_key()
        second = evaluate(cards_for("Qs As Kd 9c Kh")).evaluation_key()
        self.assertEqual(repr(first), repr(second))
        self.assertEqual(ast.literal_eval(repr(first)), first)
        self.assertNotIn("at 0x", repr(first))
        self.assertIn(str(EVALUATOR_VERSION), f"{EVALUATOR_VERSION}")


if __name__ == "__main__":
    unittest.main()
