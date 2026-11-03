"""Combinatorics-layer tests for POKER-MATH-COMBOS-001.

Pins the five public functions of ``poker.math.combinatorics`` (semantics
frozen by the committed planner handoff) with oracles that are entirely
in-test: membership over ``itertools.combinations(DECK, 2)``, an independent
structural rank/suit classifier, per-fixture brute force over the live deck,
and exact ``Fraction`` sums. No module internals are imported or probed.

Validation errors are asserted as the ``VALIDATION_ERRORS = (ValueError,
TypeError)`` type-in-tuple convention mirroring ``tests/math/test_precision.py``
— the concrete exception class is deliberately never pinned. No float/Decimal
arithmetic appears in any expectation (Decimal appears only as a rejection
input).
"""

from __future__ import annotations

import itertools
import unittest
from decimal import Decimal
from fractions import Fraction

from poker.domain.cards import DECK, Card
from poker.math.combinatorics import (
    class_counts,
    class_remaining_count,
    combinations_count,
    enumerate_starting_hands,
    weighted_combo_mass,
)

VALIDATION_ERRORS = (ValueError, TypeError)

# ---------------------------------------------------------------------------
# In-test oracles (never importing combinatorics internals).
# ---------------------------------------------------------------------------

#: ORACLE-DISTINCT-3: the unfiltered DECK combinations list, DECK i<j order.
_ORACLE_ALL_PAIRS: list[tuple[Card, Card]] = list(itertools.combinations(DECK, 2))

#: DECK symbol -> index, for the strict i<j order pin.
_SYMBOL_INDEX: dict[str, int] = {card.symbol: i for i, card in enumerate(DECK)}

_DEAD_SETS: tuple[tuple[Card, ...], ...] = (
    (),
    (Card("A", "h"),),
    (Card("A", "h"), Card("K", "d")),
    (Card("A", "h"), Card("K", "d"), Card("Q", "c")),
    (
        Card("A", "h"),
        Card("K", "d"),
        Card("Q", "c"),
        Card("7", "s"),
        Card("2", "h"),
    ),
    (Card("A", "s"), Card("A", "d"), Card("A", "c")),
    (
        Card("A", "h"),
        Card("K", "h"),
        Card("Q", "h"),
        Card("J", "h"),
        Card("T", "h"),
    ),
    tuple(DECK)[:-1],  # 51 distinct dead cards -> live == 1
    tuple(DECK),  # 52 distinct dead cards -> live == 0
)


def _oracle_classify(a: Card, b: Card) -> str:
    """ORACLE-DISTINCT-2: independent structural classifier (double loop base)."""
    if a.rank == b.rank:
        return "pair"
    if a.suit == b.suit:
        return "suited"
    return "offsuit"


def _oracle_enumeration(dead_cards: tuple[Card, ...]) -> list[tuple[Card, Card]]:
    """ORACLE-DISTINCT-3: ordered expectation, filtered DECK-order pairs."""
    return [
        pair
        for pair in _ORACLE_ALL_PAIRS
        if pair[0] not in set(dead_cards) and pair[1] not in set(dead_cards)
    ]


def _oracle_live_candidates(dead_cards: tuple[Card, ...]) -> list[tuple[Card, Card]]:
    """ORACLE-DISTINCT-3: brute force over ``live`` with strict i<j order."""
    live = [card for card in DECK if card not in set(dead_cards)]
    return list(itertools.combinations(live, 2))


def _oracle_class_counts(dead_cards: tuple[Card, ...]) -> dict[str, int]:
    counts = {"pair": 0, "suited": 0, "offsuit": 0}
    for a, b in _oracle_live_candidates(dead_cards):
        counts[_oracle_classify(a, b)] += 1
    return counts


def _oracle_class_remaining(
    dead_cards: tuple[Card, ...],
    class_combo: tuple[Card, Card],
) -> int:
    """ORACLE-DISTINCT-3: live candidates in class_combo's rank-qualified class.

    The class identity combines rank participation (the pair rank, or the two
    rank set) with the structural suit relation, reproducing the designed
    per-class totals: 6 per pair rank, 4 suited / 12 offsuit per two-rank
    class.
    """
    reference_a, reference_b = class_combo
    rank_pair = frozenset((reference_a.rank, reference_b.rank))
    same_rank = reference_a.rank == reference_b.rank
    same_suit = reference_a.suit == reference_b.suit
    return sum(
        1
        for a, b in _oracle_live_candidates(dead_cards)
        if frozenset((a.rank, b.rank)) == rank_pair
        and (a.rank == b.rank) == same_rank
        and (a.suit == b.suit) == same_suit
    )


def _unordered_key(pair: tuple[Card, Card]) -> frozenset[str]:
    return frozenset((pair[0].symbol, pair[1].symbol))


# ---------------------------------------------------------------------------
# combinations_count
# ---------------------------------------------------------------------------


class CombinationsCountTests(unittest.TestCase):
    """Exact nCk as a plain int with fail-closed value/type validation."""

    def test_small_cases_exact_int(self) -> None:
        for n, k, expected in ((4, 2, 6), (5, 3, 10), (0, 0, 1), (1, 1, 1), (2, 0, 1)):
            with self.subTest(n=n, k=k):
                actual = combinations_count(n, k)
                self.assertEqual(expected, actual)
                self.assertIs(type(actual), int)

    def test_nck_52_2_is_1326(self) -> None:
        actual = combinations_count(52, 2)
        self.assertEqual(1326, actual)
        self.assertIs(type(actual), int)

    def test_negative_n_or_k_or_k_gt_n_value_error(self) -> None:
        for n, k in ((-1, 0), (0, -1), (-5, 2), (3, 4), (5, 6)):
            with self.subTest(n=n, k=k):
                with self.assertRaises(VALIDATION_ERRORS):
                    combinations_count(n, k)

    def test_type_rejections(self) -> None:
        for bad in (True, False, 1.0, 2.0, "3", None):
            with self.subTest(n=bad):
                with self.assertRaises(VALIDATION_ERRORS):
                    combinations_count(bad, 2)
            with self.subTest(k=bad):
                with self.assertRaises(VALIDATION_ERRORS):
                    combinations_count(5, bad)


# ---------------------------------------------------------------------------
# enumerate_starting_hands
# ---------------------------------------------------------------------------


class EnumerateTests(unittest.TestCase):
    """Full/dead-set enumeration: identity, determinism, order and validation."""

    def test_empty_dead_yields_1326_unique_unordered_combos(self) -> None:
        result = enumerate_starting_hands(())
        self.assertIsInstance(result, tuple)
        self.assertEqual(1326, len(result))
        expected_keys = {_unordered_key(pair) for pair in _ORACLE_ALL_PAIRS}
        actual_keys = set()
        for pair in result:
            self.assertEqual(2, len(pair))
            a, b = pair
            self.assertIsInstance(a, Card)
            self.assertIsInstance(b, Card)
            self.assertNotEqual((a.rank, a.suit), (b.rank, b.suit))
            actual_keys.add(_unordered_key(pair))
        self.assertEqual(expected_keys, actual_keys)
        self.assertEqual(1326, len(actual_keys))

    def test_determinism_two_calls_equal_elementwise(self) -> None:
        # ORACLE-DISTINCT-6: tuple equality of two independent calls.
        self.assertEqual(
            list(enumerate_starting_hands(())), list(enumerate_starting_hands(()))
        )
        dead = (Card("A", "h"), Card("K", "d"))
        self.assertEqual(
            list(enumerate_starting_hands(dead)), list(enumerate_starting_hands(dead))
        )

    def test_deck_index_order_strict_i_lt_j(self) -> None:
        result = enumerate_starting_hands(())
        for pair in result:
            with self.subTest(pair=pair):
                self.assertIsInstance(pair, tuple)
                self.assertEqual(2, len(pair))
                self.assertIsInstance(pair[0], Card)
                self.assertIsInstance(pair[1], Card)
                self.assertLess(
                    _SYMBOL_INDEX[pair[0].symbol], _SYMBOL_INDEX[pair[1].symbol]
                )

    def test_matches_bruteforce_for_fixtures(self) -> None:
        for dead in _DEAD_SETS:
            with self.subTest(dead=dead):
                result = enumerate_starting_hands(dead)
                expected = _oracle_enumeration(dead)
                self.assertEqual(len(expected), len(result))
                for pair in result:
                    self.assertEqual(2, len(pair))
                    a, b = pair
                    self.assertNotEqual((a.rank, a.suit), (b.rank, b.suit))
                self.assertEqual(
                    {_unordered_key(pair) for pair in expected},
                    {_unordered_key(pair) for pair in result},
                )

    def test_live_below_two_returns_empty_tuple(self) -> None:
        self.assertEqual((), enumerate_starting_hands(tuple(DECK)[:-1]))
        self.assertEqual((), enumerate_starting_hands(tuple(DECK)))

    def test_dead_cards_duplicates_value_error(self) -> None:
        card_a = Card("A", "s")
        with self.assertRaises(VALIDATION_ERRORS):
            enumerate_starting_hands((card_a, Card("A", "s")))
        with self.assertRaises(VALIDATION_ERRORS):
            enumerate_starting_hands((card_a, Card("A", "s"), Card("K", "h")))

    def test_dead_cards_rejects_non_card(self) -> None:
        for dead in (("As",), (None,), (Card("A", "s"), "Kh")):
            with self.subTest(dead=dead):
                with self.assertRaises(VALIDATION_ERRORS):
                    enumerate_starting_hands(dead)


# ---------------------------------------------------------------------------
# class_counts
# ---------------------------------------------------------------------------


class ClassCountsTests(unittest.TestCase):
    """Structural class aggregates with exact keys and brute-force equality."""

    def test_empty_dead_aggregate_class_counts_and_exact_keys(self) -> None:
        result = class_counts(())
        self.assertEqual({"pair": 78, "suited": 312, "offsuit": 936}, result)
        self.assertEqual({"pair", "suited", "offsuit"}, set(result))
        self.assertEqual(3, len(result))

    def test_counts_sum_to_live_enumeration_length_all_fixtures(self) -> None:
        for dead in _DEAD_SETS:
            with self.subTest(dead=dead):
                self.assertEqual(
                    len(_oracle_enumeration(dead)),
                    sum(class_counts(dead).values()),
                )
                self.assertEqual(
                    len(enumerate_starting_hands(dead)),
                    sum(class_counts(dead).values()),
                )

    def test_representative_classes_via_structure_oracle_ac2(self) -> None:
        all_pairs = enumerate_starting_hands(())
        aa_pairs = [
            (a, b) for a, b in all_pairs if a.rank == "A" and b.rank == "A"
        ]
        ak_suited = [
            (a, b)
            for a, b in all_pairs
            if {a.rank, b.rank} == {"A", "K"} and a.suit == b.suit
        ]
        ak_offsuit = [
            (a, b)
            for a, b in all_pairs
            if {a.rank, b.rank} == {"A", "K"} and a.suit != b.suit
        ]
        self.assertEqual(6, len(aa_pairs))
        self.assertEqual(4, len(ak_suited))
        self.assertEqual(12, len(ak_offsuit))
        # Per-class structural totals pinned against the module for one
        # representative class of each shape (AC2).
        self.assertEqual(
            6,
            class_remaining_count((), (Card("A", "s"), Card("A", "d"))),
        )
        self.assertEqual(
            4,
            class_remaining_count((), (Card("A", "h"), Card("K", "h"))),
        )
        self.assertEqual(
            12,
            class_remaining_count((), (Card("A", "h"), Card("K", "d"))),
        )

    def test_structural_partition_consistency_all_1326(self) -> None:
        counts = {"pair": 0, "suited": 0, "offsuit": 0}
        for a, b in _ORACLE_ALL_PAIRS:
            counts[_oracle_classify(a, b)] += 1
        self.assertEqual(counts, class_counts(()))
        self.assertEqual(1326, sum(class_counts(()).values()))

    def test_fixture_class_counts_equal_bruteforce_fixtures(self) -> None:
        for dead in _DEAD_SETS:
            with self.subTest(dead=dead):
                self.assertEqual(_oracle_class_counts(dead), class_counts(dead))


# ---------------------------------------------------------------------------
# class_remaining_count
# ---------------------------------------------------------------------------


class ClassRemainingCountTests(unittest.TestCase):
    """Card removal per structural class, validated pair argument, dead combos."""

    def test_representative_cases_across_fixtures(self) -> None:
        cases = (
            ((), (Card("A", "s"), Card("A", "d"))),
            ((Card("A", "s"), Card("A", "c")), (Card("A", "s"), Card("A", "d"))),
            ((Card("A", "h"),), (Card("A", "s"), Card("A", "d"))),
            ((Card("A", "h"), Card("K", "d")), (Card("A", "h"), Card("K", "h"))),
            ((Card("A", "h"), Card("K", "d")), (Card("A", "h"), Card("K", "d"))),
        )
        for index, (dead, class_combo) in enumerate(cases):
            with self.subTest(index=index):
                actual = class_remaining_count(dead, class_combo)
                self.assertEqual(
                    _oracle_class_remaining(dead, class_combo), actual
                )

    def test_all_four_aces_dead_pair_class_zero(self) -> None:
        dead = (
            Card("A", "s"),
            Card("A", "d"),
            Card("A", "c"),
            Card("A", "h"),
        )
        self.assertEqual(0, class_remaining_count(dead, (Card("A", "s"), Card("A", "d"))))
        self.assertEqual(
            _oracle_class_counts(dead)["pair"], class_counts(dead)["pair"]
        )
        # Aggregate invariant: the four aces being dead leaves 12 live ranks;
        # 6 AA combos each, and the AA class specifically is gone entirely.
        self.assertEqual(12, _oracle_class_counts(dead)["pair"] // 6)

    def test_class_combo_may_be_dead_defines_class_only(self) -> None:
        dead = (Card("A", "h"), Card("K", "h"))
        class_combo = (Card("A", "h"), Card("K", "h"))
        actual = class_remaining_count(dead, class_combo)
        self.assertEqual(_oracle_class_remaining(dead, class_combo), actual)
        # The class is defined structurally (suited): 3 remaining suited AK
        # combos once Ah and Kh are dead.
        self.assertEqual(3, actual)

    def test_class_combo_validation_errors(self) -> None:
        valid = (Card("A", "s"), Card("A", "d"))
        for bad_cc in (
            (Card("A", "s"), Card("A", "s")),
            (Card("A", "s"),),
            (Card("A", "s"), Card("A", "d"), Card("Q", "c")),
            ("As", "Kh"),
            (Card("A", "s"), None),
        ):
            with self.subTest(cc=bad_cc):
                with self.assertRaises(VALIDATION_ERRORS):
                    class_remaining_count((), bad_cc)
        # dead_cards validation is reused for the first argument.
        with self.assertRaises(VALIDATION_ERRORS):
            class_remaining_count(
                (Card("A", "s"), Card("A", "s")), valid
            )
        with self.assertRaises(VALIDATION_ERRORS):
            class_remaining_count((None,), valid)
        # Duplicate class_combo validation happens even when dead_cards is empty.
        with self.assertRaises(VALIDATION_ERRORS):
            class_remaining_count((), (Card("A", "s"), Card("A", "s")))


# ---------------------------------------------------------------------------
# weighted_combo_mass
# ---------------------------------------------------------------------------


def _distinct_weighted_combos() -> list[tuple[Card, Card]]:
    """Three structurally distinct combos for exact-mass oracles."""
    return [
        (Card("A", "s"), Card("A", "d")),
        (Card("A", "c"), Card("K", "h")),
        (Card("A", "h"), Card("K", "h")),
    ]


class WeightedComboMassTests(unittest.TestCase):
    """Exact unnormalized Fraction mass with fail-closed intake validation."""

    def test_int_weights_exact_sum_denominator_one(self) -> None:
        result = weighted_combo_mass(_distinct_weighted_combos(), [2, 3, 4])
        self.assertEqual(Fraction(9), result)
        self.assertIs(type(result), Fraction)
        self.assertEqual(9, result.numerator)
        self.assertEqual(1, result.denominator)

    def test_fraction_weights_exact_sum_preserves_denominator(self) -> None:
        weights = [Fraction(1, 3), Fraction(1, 6), 2]
        result = weighted_combo_mass(_distinct_weighted_combos(), weights)
        expected = Fraction(1, 3) + Fraction(1, 6) + Fraction(2)
        self.assertEqual(Fraction(5, 2), result)
        self.assertEqual(expected, result)
        self.assertEqual((5, 2), (result.numerator, result.denominator))
        self.assertIs(type(result), Fraction)

    def test_empty_combos_and_weights_zero(self) -> None:
        result = weighted_combo_mass([], [])
        self.assertEqual(Fraction(0), result)
        self.assertIs(type(result), Fraction)
        self.assertEqual(1, result.denominator)

    def test_weight_type_rejections(self) -> None:
        for bad in (True, False, 0.5, 1.0, Decimal("1"), "x"):
            with self.subTest(weight=bad):
                with self.assertRaises(VALIDATION_ERRORS):
                    weighted_combo_mass(
                        [(Card("A", "s"), Card("A", "d"))], [bad]
                    )

    def test_negative_weight_value_error(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            weighted_combo_mass([(Card("A", "s"), Card("A", "d"))], [-1])
        with self.assertRaises(VALIDATION_ERRORS):
            weighted_combo_mass(
                [(Card("A", "s"), Card("A", "d"))], [Fraction(-1, 3)]
            )

    def test_length_mismatch_value_error(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            weighted_combo_mass([(Card("A", "s"), Card("A", "d"))], [2, 3])
        with self.assertRaises(VALIDATION_ERRORS):
            weighted_combo_mass(
                [(Card("A", "s"), Card("A", "d")), (Card("A", "c"), Card("K", "h"))],
                [2],
            )

    def test_duplicate_combo_value_error(self) -> None:
        card_a = Card("A", "h")
        card_b = Card("K", "d")
        with self.assertRaises(VALIDATION_ERRORS):
            weighted_combo_mass([(card_a, card_b), (card_a, card_b)], [1, 2])
        # Unordered card-set identity: (a, b) and (b, a) are duplicates because
        # rank/suit equality is symmetric, so the structural combo is the same.
        with self.assertRaises(VALIDATION_ERRORS):
            weighted_combo_mass([(card_a, card_b), (card_b, card_a)], [1, 2])

    def test_non_card_combo_entry_rejected(self) -> None:
        for bad_combo in (
            ("As", "Kh"),
            (Card("A", "s"), None),
            (None, Card("A", "s")),
        ):
            with self.subTest(combo=bad_combo):
                with self.assertRaises(VALIDATION_ERRORS):
                    weighted_combo_mass([bad_combo], [1])

    def test_zero_weight_entry_legal(self) -> None:
        combos = [(Card("A", "s"), Card("A", "d")), (Card("A", "h"), Card("K", "d"))]
        self.assertEqual(
            Fraction(5), weighted_combo_mass(combos, [0, 5])
        )
        self.assertEqual(
            Fraction(1, 2),
            weighted_combo_mass(combos, [Fraction(0), Fraction(1, 2)]),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
