"""Range-model tests for POKER-RANGE-MODEL-001.

Pins the frozen public API of ``poker.range.model`` (semantics frozen by the
committed planner handoff) with oracles that are entirely in-test: membership
over ``itertools.combinations(DECK, 2)``, deck-index canonicalization from
the committed ``DECK`` order, an independent structural classifier built by
double loop over ``DECK`` attributes, per-fixture brute force over the live
deck and exact ``Fraction`` sums. No model internals and no combinatorics
private helpers are imported or probed; module-level oracle construction
touches only ``DECK``/``RANKS``/``combinations``/``Fraction`` and every model
call happens inside a test method, so a ``NotImplementedError`` skeleton
fails each test at its first behavioral assertion (behavioral RED with zero
collection errors).

Validation errors are asserted via the ``VALIDATION_ERRORS = (ValueError,
TypeError)`` type-in-tuple convention mirroring ``tests/math/
test_combinatorics.py`` — the concrete exception class is deliberately never
pinned except where the contract pins ``ValueError`` (len mismatch, weight
bounds, zero-range normalization, empty live deck, wrong schema_version).
No float/Decimal arithmetic appears in any expectation (``Decimal`` appears
only as a rejection input); every measured quantity is an exact
``Fraction``.
"""

from __future__ import annotations

import itertools
import json
import unittest
from decimal import Decimal
from fractions import Fraction
from unittest import mock

from poker.domain import game
from poker.domain.cards import DECK, RANKS, Card
from poker.range.model import Range

VALIDATION_ERRORS = (ValueError, TypeError)

# ---------------------------------------------------------------------------
# In-test oracles (never importing range-model internals or combinatorics
# private helpers).
# ---------------------------------------------------------------------------

#: ORACLE-DISTINCT-3: the unfiltered DECK combinations list, DECK i<j order
#: (exactly 1,326 unordered pairs).
_ORACLE_ALL_PAIRS: list[tuple[Card, Card]] = list(itertools.combinations(DECK, 2))

#: DECK symbol -> index, for the strict i<j canonical-order pin.
_SYMBOL_INDEX: dict[str, int] = {card.symbol: i for i, card in enumerate(DECK)}

#: RANKS index, for high>low structural-class key ordering.
_RANK_INDEX: dict[str, int] = {rank: i for i, rank in enumerate(RANKS)}


def _canon(a: Card, b: Card) -> tuple[Card, Card]:
    """ORACLE canonical pair: tuple sorted deck-index ascending."""
    return tuple(sorted((a, b), key=lambda card: _SYMBOL_INDEX[card.symbol]))


def _oracle_live_pairs(dead_cards: tuple[Card, ...]) -> list[tuple[Card, Card]]:
    """ORACLE: expected live combos for a dead-card set."""
    dead = frozenset(dead_cards)
    return [
        pair
        for pair in _ORACLE_ALL_PAIRS
        if pair[0] not in dead and pair[1] not in dead
    ]


def _oracle_classify(a: Card, b: Card) -> str:
    """ORACLE-DISTINCT-2: independent structural classifier."""
    if a.rank == b.rank:
        return "pair"
    if a.suit == b.suit:
        return "suited"
    return "offsuit"


def _build_oracle_class_keys() -> list[tuple[str, str, str]]:
    """All 169 structural class keys: 13 pair + 78 suited + 78 offsuit."""
    keys: list[tuple[str, str, str]] = []
    for rank in RANKS:
        keys.append((rank, rank, "pair"))
    for high in RANKS:
        for low in RANKS[: _RANK_INDEX[high]]:
            keys.append((high, low, "suited"))
            keys.append((high, low, "offsuit"))
    return keys


def _oracle_class_key(a: Card, b: Card) -> tuple[str, str, str]:
    """ORACLE class key for one combo (independent double-loop base)."""
    kind = _oracle_classify(a, b)
    if kind == "pair":
        return (a.rank, a.rank, "pair")
    first, second = sorted(
        (a.rank, b.rank), key=_RANK_INDEX.__getitem__, reverse=True
    )
    return (first, second, kind)


def _range_table(range_obj) -> dict[tuple[Card, Card], Fraction]:
    """Per-combo weight table over all 1,326 oracle pairs (model calls)."""
    return {pair: range_obj.weight(pair) for pair in _ORACLE_ALL_PAIRS}


# ---------------------------------------------------------------------------
# Default constructor (C01)
# ---------------------------------------------------------------------------


class RangeDefaultTests(unittest.TestCase):
    """The empty Range is the legal all-1326 zero range."""

    def test_default_range_is_all_1326_zero_mass(self) -> None:
        r = Range()
        sampled = (
            _ORACLE_ALL_PAIRS[0],
            _canon(Card("A", "s"), Card("K", "s")),
            _canon(Card("2", "h"), Card("7", "d")),
            _ORACLE_ALL_PAIRS[-1],
        )
        for pair in sampled:
            with self.subTest(pair=pair):
                self.assertIsInstance(pair[0], Card)
                self.assertIsInstance(pair[1], Card)
                self.assertEqual(Fraction(0), r.weight(pair))
                self.assertIs(type(r.weight(pair)), Fraction)
        zero_count = sum(1 for pair in _ORACLE_ALL_PAIRS if r.weight(pair) == 0)
        self.assertEqual(1326, zero_count)
        self.assertEqual(Fraction(0), r.total_mass())
        self.assertIs(type(r.total_mass()), Fraction)


# ---------------------------------------------------------------------------
# Constructor validation and canonicalization (C02, C03)
# ---------------------------------------------------------------------------


class RangeConstructorTests(unittest.TestCase):
    """Paired-sequence construction with fail-closed intake validation."""

    def test_constructor_parity_and_pair_rejections(self) -> None:
        pair = (Card("A", "s"), Card("K", "d"))
        with self.assertRaises(ValueError):
            Range([pair], [1, 2])
        with self.assertRaises(ValueError):
            Range([pair], [])
        for bad_combo, weights in (
            ((Card("A", "s"),), [1]),
            ((Card("A", "s"), Card("K", "d"), Card("K", "h")), [1]),
            ((Card("A", "s"), Card("A", "s")), [1]),
            (("As", "Kd"), [1]),
            ((Card("A", "s"), None), [1]),
        ):
            with self.subTest(combo=bad_combo):
                with self.assertRaises(VALIDATION_ERRORS):
                    Range([bad_combo], weights)

    def test_constructor_canonicalizes_permutations(self) -> None:
        r = Range(
            [(Card("K", "s"), Card("A", "s")), (Card("A", "d"), Card("K", "d"))],
            [1, Fraction(1, 2)],
        )
        both_orders = (
            (Card("A", "s"), Card("K", "s")),
            (Card("K", "s"), Card("A", "s")),
        )
        for pair in both_orders:
            with self.subTest(pair=pair):
                self.assertEqual(Fraction(1), r.weight(pair))
        both_orders_kd = (
            (Card("A", "d"), Card("K", "d")),
            (Card("K", "d"), Card("A", "d")),
        )
        for pair in both_orders_kd:
            with self.subTest(pair=pair):
                self.assertEqual(Fraction(1, 2), r.weight(pair))
        self.assertEqual(Fraction(3, 2), r.total_mass())
        self.assertEqual(Fraction(0), r.weight((Card("Q", "d"), Card("Q", "c"))))


# ---------------------------------------------------------------------------
# Weight validation (C04, C05)
# ---------------------------------------------------------------------------


class RangeWeightValidationTests(unittest.TestCase):
    """Strict weight identity/types and inclusive [0, 1] bounds."""

    def test_weight_type_rejections(self) -> None:
        pair = (Card("A", "s"), Card("K", "d"))
        for bad in (True, False, 0.5, 1.0, Decimal("1"), "0.5", None):
            with self.subTest(weight=bad):
                with self.assertRaises(VALIDATION_ERRORS):
                    Range([pair], [bad])
                with self.assertRaises(VALIDATION_ERRORS):
                    Range().set_weight(pair, bad)

    def test_weight_bounds(self) -> None:
        pair = (Card("A", "s"), Card("K", "d"))
        for bad in (-1, 2, Fraction(-1, 10), Fraction(3, 2)):
            with self.subTest(weight=bad):
                with self.assertRaises(ValueError):
                    Range([pair], [bad])
        for good in (0, 1, Fraction(1, 2), Fraction(0)):
            with self.subTest(weight=good):
                r = Range([pair], [good])
                actual = r.weight(pair)
                self.assertEqual(Fraction(good), actual)
                self.assertIs(type(actual), Fraction)


# ---------------------------------------------------------------------------
# uniform (C06..C11)
# ---------------------------------------------------------------------------


class RangeUniformTests(unittest.TestCase):
    """Delegated live enumeration with exact 1/len uniform mass."""

    def test_uniform_full_deck(self) -> None:
        r = Range.uniform()
        self.assertEqual(Fraction(1), r.total_mass())
        for pair in _ORACLE_ALL_PAIRS:
            with self.subTest(pair=pair):
                self.assertEqual(Fraction(1, 1326), r.weight(pair))
                self.assertIs(type(r.weight(pair)), Fraction)

    def test_uniform_dead_ace(self) -> None:
        ace = Card("A", "s")
        r = Range.uniform(dead_cards=(ace,))
        live_pairs = _oracle_live_pairs((ace,))
        self.assertEqual(1275, len(live_pairs))
        for pair in _ORACLE_ALL_PAIRS:
            touches = pair[0] == ace or pair[1] == ace
            expected = Fraction(0) if touches else Fraction(1, 1275)
            with self.subTest(pair=pair):
                self.assertEqual(expected, r.weight(pair))
        self.assertEqual(Fraction(1), r.total_mass())

    def test_uniform_multi_dead_brute_force(self) -> None:
        fixtures = (
            (Card("A", "h"), Card("K", "d"), Card("Q", "c")),
            (Card("A", "s"), Card("A", "d"), Card("A", "c")),
        )
        for dead in fixtures:
            with self.subTest(dead=dead):
                r = Range.uniform(dead_cards=dead)
                live = _oracle_live_pairs(dead)
                expected_live_count = ((52 - len(dead)) * (51 - len(dead))) // 2
                self.assertEqual(expected_live_count, len(live))
                self.assertEqual(1176, len(live))
                weight = Fraction(1, len(live))
                live_keys = {_canon(a, b) for a, b in live}
                positive = 0
                for pair in _ORACLE_ALL_PAIRS:
                    canonical = _canon(*pair)
                    expected = weight if canonical in live_keys else Fraction(0)
                    if r.weight(canonical) != 0:
                        positive += 1
                    self.assertEqual(expected, r.weight(canonical))
                self.assertEqual(expected_live_count, positive)
                self.assertEqual(Fraction(1), r.total_mass())

    def test_uniform_dead_card_validation(self) -> None:
        for dead in (
            (Card("A", "h"), "Kd"),
            (Card("A", "h"), Card("A", "h")),
        ):
            with self.subTest(dead=dead):
                with self.assertRaises(VALIDATION_ERRORS):
                    Range.uniform(dead_cards=dead)

    def test_uniform_delegates_to_enumerate_starting_hands(self) -> None:
        as_ks = (Card("A", "s"), Card("K", "s"))
        qd_qc = (Card("Q", "d"), Card("Q", "c"))
        with mock.patch(
            "poker.range.model.enumerate_starting_hands",
            return_value=(as_ks, qd_qc),
        ) as mocked:
            r = Range.uniform()
        mocked.assert_called_once_with(())
        self.assertEqual(Fraction(1, 2), r.weight(as_ks))
        self.assertEqual(Fraction(1, 2), r.weight(qd_qc))
        positive = sum(1 for pair in _ORACLE_ALL_PAIRS if r.weight(pair) != 0)
        self.assertEqual(2, positive)
        self.assertEqual(Fraction(1), r.total_mass())

    def test_uniform_empty_live_deck_fails_closed(self) -> None:
        # Fail-closed normalization: the live enumeration
        # (enumerate_starting_hands) is empty whenever fewer than two live
        # cards remain. With 52 dead cards zero live cards remain; with 51
        # dead cards exactly one live card remains, hence ZERO live combos
        # (len(live enumeration) == 0), and the pinned contract raises
        # ValueError because Fraction(1 / len(live)) cannot be defined. The
        # designer note "no raise with 51 dead (live==1)" counts live cards,
        # but there are no live two-card combos to weight, so both cases
        # fail closed under the pinned delegation contract.
        for dead in (tuple(DECK), tuple(DECK)[:-1]):
            with self.subTest(dead_len=len(dead)):
                with self.assertRaises(ValueError):
                    Range.uniform(dead_cards=dead)


# ---------------------------------------------------------------------------
# condition (C12..C14)
# ---------------------------------------------------------------------------


class RangeConditionTests(unittest.TestCase):
    """Dead-card conditioning: zero-and-KEEP semantics, value objects."""

    def test_condition_dead_card_zeroes_kept_entries(self) -> None:
        ah = Card("A", "h")
        r = Range.uniform()
        rc = r.condition((ah,))
        touching = [pair for pair in _ORACLE_ALL_PAIRS if ah in pair]
        self.assertEqual(51, len(touching))
        for pair in _ORACLE_ALL_PAIRS:
            expected = Fraction(0) if pair in touching else Fraction(1, 1326)
            with self.subTest(pair=pair):
                self.assertEqual(expected, rc.weight(pair))
        # Kept as zero-mass entries: spot-check one Ah combo stays readable.
        self.assertEqual(Fraction(0), rc.weight((Card("2", "h"), ah)))
        self.assertEqual(Fraction(1275, 1326), rc.total_mass())
        # New instance: the original range is untouched.
        self.assertIsNot(r, rc)
        self.assertIsInstance(rc, Range)
        self.assertEqual(Fraction(1), r.total_mass())

    def test_condition_idempotent_and_repeat_dead(self) -> None:
        ah = Card("A", "h")
        rc = Range.uniform().condition((ah,))
        rc2 = rc.condition((ah,))
        self.assertEqual(_range_table(rc), _range_table(rc2))

    def test_condition_preserves_other_weights_custom(self) -> None:
        kd = Card("K", "d")
        r = Range(
            [
                (Card("A", "s"), Card("K", "s")),
                (Card("Q", "d"), Card("Q", "c")),
                (Card("A", "d"), Card("K", "d")),
            ],
            [1, 1, Fraction(1, 2)],
        )
        rc = r.condition((kd,))
        kd_touching = [pair for pair in _ORACLE_ALL_PAIRS if kd in pair]
        for pair in kd_touching:
            with self.subTest(pair=pair):
                self.assertEqual(Fraction(0), rc.weight(pair))
        # Surviving positive weights exact; survives mass == Fraction(2).
        self.assertEqual(Fraction(1), rc.weight((Card("A", "s"), Card("K", "s"))))
        self.assertEqual(Fraction(1), rc.weight((Card("Q", "d"), Card("Q", "c"))))
        self.assertEqual(Fraction(2), rc.total_mass())
        # Original untouched: (Ad,Kd) still carries Fraction(1,2).
        self.assertEqual(Fraction(1, 2), r.weight((Card("A", "d"), Card("K", "d"))))
        self.assertEqual(Fraction(5, 2), r.total_mass())


# ---------------------------------------------------------------------------
# weight / set_weight (C15..C17)
# ---------------------------------------------------------------------------


class RangeWeightAccessTests(unittest.TestCase):
    """Canonicalizing reads and value-object writes with chaining."""

    def test_weight_read_canonicalization_legality(self) -> None:
        zero = Range()
        uniform = Range.uniform()
        self.assertEqual(
            uniform.weight((Card("K", "s"), Card("A", "s"))),
            uniform.weight((Card("A", "s"), Card("K", "s"))),
        )
        self.assertEqual(Fraction(0), zero.weight((Card("A", "s"), Card("K", "s"))))
        self.assertEqual(
            Fraction(1, 1326), uniform.weight((Card("K", "s"), Card("A", "s")))
        )
        for bad in (
            (Card("A", "s"), Card("A", "s")),
            (Card("A", "s"),),
            ("As", "Ks"),
            (Card("A", "s"), Card("K", "d"), Card("Q", "c")),
        ):
            with self.subTest(combo=bad):
                with self.assertRaises(VALIDATION_ERRORS):
                    uniform.weight(bad)

    def test_set_weight_returns_new_instance(self) -> None:
        pair = (Card("A", "s"), Card("K", "s"))
        r0 = Range()
        r1 = r0.set_weight(pair, 1)
        self.assertIsNot(r0, r1)
        self.assertIsInstance(r1, Range)
        self.assertEqual(Fraction(1), r1.weight(pair))
        self.assertEqual(Fraction(0), r0.weight(pair))
        self.assertEqual(Fraction(0), r1.weight((Card("A", "d"), Card("K", "d"))))

    def test_set_weight_chain_and_overwrite(self) -> None:
        as_ks = (Card("A", "s"), Card("K", "s"))
        ad_kd = (Card("A", "d"), Card("K", "d"))
        qd_qc = (Card("Q", "d"), Card("Q", "c"))
        r1 = (
            Range()
            .set_weight(as_ks, 1)
            .set_weight(ad_kd, Fraction(1, 2))
            .set_weight(qd_qc, 1)
        )
        expected = {
            _canon(*as_ks): Fraction(1),
            _canon(*ad_kd): Fraction(1, 2),
            _canon(*qd_qc): Fraction(1),
        }
        self.assertEqual(Fraction(5, 2), r1.total_mass())
        for pair in _ORACLE_ALL_PAIRS:
            with self.subTest(pair=pair):
                self.assertEqual(
                    expected.get(_canon(*pair), Fraction(0)), r1.weight(pair)
                )
        r2 = r1.set_weight(as_ks, Fraction(1, 3))
        self.assertEqual(Fraction(1, 3), r2.weight(as_ks))
        self.assertEqual(Fraction(1), r1.weight(as_ks))


# ---------------------------------------------------------------------------
# total_mass / normalized_weights (C18..C20)
# ---------------------------------------------------------------------------


class RangeMassNormalizationTests(unittest.TestCase):
    """Exact masses with fail-closed zero-range normalization."""

    def test_total_mass_exact(self) -> None:
        self.assertEqual(Fraction(0), Range().total_mass())
        r = Range(
            [(Card("A", "s"), Card("K", "s")), (Card("Q", "d"), Card("Q", "c"))],
            [Fraction(1, 3), Fraction(1, 6)],
        )
        self.assertEqual(Fraction(1, 2), r.total_mass())
        self.assertIs(type(r.total_mass()), Fraction)
        self.assertEqual(Fraction(1), Range.uniform().total_mass())

    def test_normalized_weights_zero_range_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            Range().normalized_weights()

    def test_normalized_weights_exact(self) -> None:
        # Intent of designer case C20: exact normalized targets 1/4, 1/4,
        # 1/2 with all-Fraction values summing to 1. The literal weight 2
        # from the design string violates the pinned inclusive [0, 1] weight
        # bounds (designer C05 and planner AC-N reject w > 1), so the same
        # targets are produced with the legal vector [1/2, 1/2, 1].
        as_ks = _canon(Card("A", "s"), Card("K", "s"))
        qd_qc = _canon(Card("Q", "d"), Card("Q", "c"))
        ad_kd = _canon(Card("A", "d"), Card("K", "d"))
        r = Range(
            [
                (Card("A", "s"), Card("K", "s")),
                (Card("Q", "d"), Card("Q", "c")),
                (Card("A", "d"), Card("K", "d")),
            ],
            [Fraction(1, 2), Fraction(1, 2), 1],
        )
        self.assertEqual(Fraction(2), r.total_mass())
        normalized = r.normalized_weights()
        self.assertEqual(Fraction(1, 4), normalized[as_ks])
        self.assertEqual(Fraction(1, 4), normalized[qd_qc])
        self.assertEqual(Fraction(1, 2), normalized[ad_kd])
        for value in normalized.values():
            self.assertIs(type(value), Fraction)
        self.assertEqual(Fraction(1), sum(normalized.values(), start=Fraction(0)))
        uniform_normalized = Range.uniform().normalized_weights()
        self.assertEqual(1326, len(uniform_normalized))
        for pair in _ORACLE_ALL_PAIRS:
            with self.subTest(pair=pair):
                self.assertEqual(
                    Fraction(1, 1326), uniform_normalized[_canon(*pair)]
                )


# ---------------------------------------------------------------------------
# class_groups (C21, C22)
# ---------------------------------------------------------------------------


class RangeClassGroupsTests(unittest.TestCase):
    """169 structural classes, exact masses and mass conservation."""

    def test_class_groups_uniform_169_classes(self) -> None:
        classes = Range.uniform().class_groups()
        oracle_masses = {key: Fraction(0) for key in _build_oracle_class_keys()}
        for pair in _ORACLE_ALL_PAIRS:
            oracle_masses[_oracle_class_key(*pair)] += Fraction(1, 1326)
        self.assertEqual(169, len(oracle_masses))
        self.assertEqual(oracle_masses, classes)
        for high, low, kind in classes:
            with self.subTest(key=(high, low, kind)):
                self.assertIn(kind, ("pair", "suited", "offsuit"))
                if kind == "pair":
                    self.assertEqual(high, low)
                else:
                    self.assertNotEqual(high, low)
                    self.assertGreater(_RANK_INDEX[high], _RANK_INDEX[low])
        self.assertEqual(Fraction(1), sum(classes.values(), start=Fraction(0)))
        # Full-deck uniform shape: 6/4/12 per class kind.
        pair_masses = {value for key, value in classes.items() if key[2] == "pair"}
        suited_masses = {
            value for key, value in classes.items() if key[2] == "suited"
        }
        offsuit_masses = {
            value for key, value in classes.items() if key[2] == "offsuit"
        }
        self.assertEqual({Fraction(6, 1326)}, pair_masses)
        self.assertEqual({Fraction(4, 1326)}, suited_masses)
        self.assertEqual({Fraction(12, 1326)}, offsuit_masses)

    def test_class_groups_mass_conservation_custom(self) -> None:
        as_ks = (Card("A", "s"), Card("K", "s"))
        ak_suited = ("A", "K", "suited")
        r = Range([as_ks], [1])
        before = r.class_groups()
        self.assertEqual(r.total_mass(), sum(before.values(), start=Fraction(0)))
        self.assertEqual(169, len(before))
        self.assertEqual(Fraction(1), before[ak_suited])
        zero_keys = [key for key, value in before.items() if value == 0]
        self.assertEqual(168, len(zero_keys))
        for key in zero_keys:
            self.assertEqual(Fraction(0), before[key])
        r2 = r.set_weight((Card("A", "h"), Card("K", "h")), Fraction(1, 2))
        after = r2.class_groups()
        self.assertEqual(169, len(after))
        self.assertEqual(r2.total_mass(), sum(after.values(), start=Fraction(0)))
        self.assertEqual(Fraction(3, 2), after[ak_suited])
        for key in after:
            if key != ak_suited:
                with self.subTest(key=key):
                    self.assertEqual(before[key], after[key])


# ---------------------------------------------------------------------------
# Serialization (C23..C28)
# ---------------------------------------------------------------------------


class RangeSerializationTests(unittest.TestCase):
    """Versioned exact-keys payload, byte stability, fail-closed intake."""

    def test_to_dict_structure_canonical_order_zero_excluded(self) -> None:
        r = Range(
            [(Card("K", "s"), Card("A", "s")), (Card("A", "d"), Card("K", "d"))],
            [1, Fraction(1, 2)],
        )
        payload = r.to_dict()
        self.assertEqual({"schema_version", "weights"}, set(payload))
        self.assertEqual(game.SCHEMA_VERSION, payload["schema_version"])
        expected = [
            {"cards": ["Kd", "Ad"], "weight": "1/2"},
            {"cards": ["Ks", "As"], "weight": "1"},
        ]
        self.assertEqual(expected, payload["weights"])
        indexes = [
            (_SYMBOL_INDEX[entry["cards"][0]], _SYMBOL_INDEX[entry["cards"][1]])
            for entry in payload["weights"]
        ]
        self.assertEqual(indexes, sorted(indexes))
        # Zero-weight combos are excluded from the listing.
        r_zero = r.set_weight((Card("A", "s"), Card("K", "s")), 0)
        self.assertEqual(
            [{"cards": ["Kd", "Ad"], "weight": "1/2"}], r_zero.to_dict()["weights"]
        )
        # The zero range serializes to the empty weights list.
        self.assertEqual([], Range().to_dict()["weights"])
        self.assertEqual(
            {"schema_version": game.SCHEMA_VERSION, "weights": []},
            Range().to_dict(),
        )

    def test_roundtrip_exact_and_reversed_identity(self) -> None:
        r = Range([(Card("K", "s"), Card("A", "s"))], [Fraction(1, 3)])
        r2 = Range.from_dict(json.loads(json.dumps(r.to_dict())))
        for pair in (
            (Card("A", "s"), Card("K", "s")),
            (Card("K", "s"), Card("A", "s")),
        ):
            with self.subTest(pair=pair):
                self.assertEqual(Fraction(1, 3), r2.weight(pair))
        self.assertEqual(_range_table(r), _range_table(r2))
        # Constructor vs from_dict equality in either insertion order.
        r3 = Range([(Card("A", "s"), Card("K", "s"))], [Fraction(1, 3)])
        self.assertEqual(_range_table(r), _range_table(r3))

    def test_empty_weights_roundtrip_zero_range(self) -> None:
        r = Range.from_dict(
            {"schema_version": game.SCHEMA_VERSION, "weights": []}
        )
        self.assertEqual(Fraction(0), r.total_mass())
        for pair in _ORACLE_ALL_PAIRS:
            self.assertEqual(Fraction(0), r.weight(pair))
        payload = r.to_dict()
        again = Range.from_dict(json.loads(json.dumps(payload)))
        self.assertEqual(payload, again.to_dict())

    def test_from_dict_rejections_matrix(self) -> None:
        version = game.SCHEMA_VERSION
        base_entry = {"cards": ["As", "Ks"], "weight": "1"}

        def payload(weights, **overrides):
            node = {"schema_version": version, "weights": weights}
            node.update(overrides)
            return node

        # (a) unordered duplicate entries raise.
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict(payload([base_entry, dict(base_entry, cards=["Ks", "As"])]))
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict(payload([base_entry, dict(base_entry)]))
        # (b) weight strings: malformed / negative / above one fail closed;
        #     '0' and '0.5' are legal and parse to exact Fractions.
        for weight_str in ("abc", "-1", "3/2", "2"):
            with self.subTest(weight=weight_str):
                with self.assertRaises(VALIDATION_ERRORS):
                    Range.from_dict(payload([dict(base_entry, weight=weight_str)]))
        for legal in ("0", "0.5"):
            with self.subTest(weight=legal):
                r = Range.from_dict(payload([dict(base_entry, weight=legal)]))
                self.assertEqual(
                    Fraction(legal), r.weight((Card("A", "s"), Card("K", "s")))
                )
        # (c) malformed cards fail before/independent of Card legality.
        for cards in (
            ["A"],
            ["As", "Kd", "Xx"],
            "As",
            ["ah", "Kd"],
            ["AS", "Kd"],
            ["10s", "Kd"],
            [7, "Kd"],
        ):
            with self.subTest(cards=cards):
                with self.assertRaises(VALIDATION_ERRORS):
                    Range.from_dict(payload([{"cards": cards, "weight": "1"}]))
        # (d) outer and entry keys are exact (missing / extra fail closed).
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict({"schema_version": version})
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict(payload([base_entry], extra="x"))
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict(payload([dict(base_entry, extra=1)]))
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict(payload([{"cards": ["As", "Ks"]}]))
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict(payload([{"weight": "1"}]))
        # (e) schema_version fail-closed; non-dict payload rejected.
        for bad_version in (version + 1, -1):
            with self.subTest(version=bad_version):
                with self.assertRaises(ValueError):
                    Range.from_dict(
                        {"schema_version": bad_version, "weights": [base_entry]}
                    )
        with self.assertRaises(VALIDATION_ERRORS):
            Range.from_dict([("schema_version", version)])

    def test_serialization_byte_stability_and_canonical_identity(self) -> None:
        base = Range(
            [(Card("K", "s"), Card("A", "s")), (Card("A", "d"), Card("K", "d"))],
            [Fraction(1, 3), Fraction(1, 2)],
        )
        first = json.dumps(base.to_dict(), sort_keys=True)
        second = json.dumps(base.to_dict(), sort_keys=True)
        self.assertEqual(first, second)
        # Equal-construction ranges dump byte-identically.
        roundtripped = Range.from_dict(json.loads(first))
        self.assertEqual(first, json.dumps(roundtripped.to_dict(), sort_keys=True))
        reversed_order = Range([(Card("A", "s"), Card("K", "s"))], [Fraction(1, 3)])
        self.assertEqual(
            json.dumps(
                Range([(Card("K", "s"), Card("A", "s"))], [Fraction(1, 3)]).to_dict(),
                sort_keys=True,
            ),
            json.dumps(reversed_order.to_dict(), sort_keys=True),
        )

    def test_all_quantities_are_fraction_no_float(self) -> None:
        r = Range(
            [(Card("A", "s"), Card("K", "s")), (Card("Q", "d"), Card("Q", "c"))],
            [1, Fraction(1, 2)],
        )
        uniform = Range.uniform()
        normalized = r.normalized_weights()
        for value in normalized.values():
            self.assertIs(type(value), Fraction)
        classes = r.class_groups()
        for value in classes.values():
            self.assertIs(type(value), Fraction)
        self.assertIs(type(r.total_mass()), Fraction)
        self.assertIs(type(uniform.total_mass()), Fraction)
        for pair in (
            _ORACLE_ALL_PAIRS[0],
            _canon(Card("A", "s"), Card("K", "s")),
            _ORACLE_ALL_PAIRS[-1],
        ):
            with self.subTest(pair=pair):
                self.assertIs(type(uniform.weight(pair)), Fraction)
                self.assertIs(type(r.weight(pair)), Fraction)
        for payload in (r.to_dict(), uniform.to_dict(), Range().to_dict()):
            for entry in payload["weights"]:
                self.assertIs(type(entry["weight"]), str)
                self.assertNotIsInstance(entry["weight"], float)
        # JSON round-trip keeps every weight an exact string, never a float.
        r2 = Range.from_dict(json.loads(json.dumps(r.to_dict())))
        self.assertEqual(_range_table(r), _range_table(r2))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
