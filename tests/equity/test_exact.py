"""Exact river-equity tests for POKER-EQUITY-EXACT-001 (unittest canonical).

The committed test-designer handoff pinned ``python -m pytest`` wording; the
repository's canonical runner is ``python -m unittest`` (orchestrator
mapping), so every case below is a plain ``unittest.TestCase`` method with
the same case IDs P1-P16 / N1-N14 and identical assertions (``pytest.raises``
maps to ``self.assertRaises``/``assertRaisesRegex``; no pytest fixtures —
fixtures are module-level immutable value objects or per-test builders with
no shared mutable state).

Oracle independence: the O1 oracle is an in-test brute force over
``itertools.combinations`` of ``DECK`` minus the known cards — it never
calls ``enumerate_starting_hands`` (nor any combinatorics helper) — and
treats ``evaluate7``/``compare`` as a trusted black-box substrate shared
with the module under test (planner-pinned). No ``poker.equity.exact``
internals are imported or probed; only the public API.

Fixture-verification deviations (planner risk note: verified arithmetic
replaces the designer's literal example; the tie/partition rules themselves
are untouched):
- P3 uses the designer's D1 royal-board substitution (board A K Q J T of
  spades IS the unique maximum hand; unpaired monotone board admits no full
  house or quads and the only straight-flush class 6-7-8-9-T of spades is
  below the royal; every live combo plays the board royal -> ties == 1).
- P6 substitution: the designer's opponent combo (Qs, Ks) on board
  Qh Jh Th 2d 3d is ONE pair of queens (its own note wrongly called it
  trips-Q), which LOSES to hero's two pair 3s/2s. The substituted opponent
  combo (Qs, Qc) — trips queens — preserves the designed intent (a
  single-live-combo LOSS against hero's two pair, 43 dead cards,
  enumerated == 1).
- P8 substitution: the designer's dead-combo weights 5 and 9 violate the
  Range contract's pinned [0, 1] weight bounds. Substituted legal weights
  1/2 (on a combo containing the dead 9c) and 3/4 (on a combo containing
  hero's Ah) preserve the discriminator: known-combo mass contributes 0 and
  normalization uses the LIVE mass denominator only (a global
  ``normalized_weights`` denominator of 9/4 would fail oracle equality).
- P10 note: with hero (Ah, Kh) and board (Kd, Ks, ...), every KK combo is
  necessarily dead (all four kings are known), so its 1/5 weight
  contributes 0 — another live-mass discriminator; the remaining live mass
  is 3/5 and oracle equality is the pinned assertion.
"""

from __future__ import annotations

import dataclasses
import itertools
import unittest
from fractions import Fraction
from pathlib import Path

from poker.cards.evaluator import compare, evaluate7
from poker.domain.cards import DECK, Card
from poker.equity import exact as exact_module
from poker.equity.exact import EQUITY_EXACT_VERSION, EquityResult, exact_river_equity
from poker.equity import (
    EQUITY_EXACT_VERSION as _REEXPORTED_VERSION,
    EquityResult as _ReexportedEquityResult,
    exact_river_equity as _reexported_function,
)
from poker.range.model import Range

VALIDATION_ERRORS = (ValueError, TypeError)

_SYMBOL_TO_CARD: dict[str, Card] = {card.symbol: card for card in DECK}

#: Shared trusted black-box substrate import aliases (planner-pinned).
_evaluate7 = evaluate7
_compare = compare

#: Board reused by P4/P5/P8 (and P6's twin board with 2d/3d).
_FLOOD_BOARD: tuple[Card, ...] = tuple(
    _SYMBOL_TO_CARD[symbol] for symbol in ("Qh", "Jh", "Th", "2c", "3c")
)
_FLOOD_BOARD_D23: tuple[Card, ...] = tuple(
    _SYMBOL_TO_CARD[symbol] for symbol in ("Qh", "Jh", "Th", "2d", "3d")
)
_TRIP_K_BOARD: tuple[Card, ...] = tuple(
    _SYMBOL_TO_CARD[symbol] for symbol in ("Kd", "Ks", "7c", "2d", "3s")
)
_ACES_BOARD: tuple[Card, ...] = tuple(
    _SYMBOL_TO_CARD[symbol] for symbol in ("Kc", "Qc", "7d", "7h", "2s")
)


def _cards(*symbols: str) -> tuple[Card, ...]:
    return tuple(_SYMBOL_TO_CARD[symbol] for symbol in symbols)


def _dead_excluding(*kept_symbols: str) -> tuple[Card, ...]:
    """Every DECK card except the given symbols (deterministic DECK order)."""
    keep = frozenset(kept_symbols)
    return tuple(card for card in DECK if card.symbol not in keep)


def _single_live_range(*combo_symbols: str) -> Range:
    combo = _cards(*combo_symbols)
    return Range().set_weight(combo, Fraction(1))


def _pair(*symbols: str) -> tuple[Card, Card]:
    pair = _cards(*symbols)
    assert len(pair) == 2
    return (pair[0], pair[1])


# ---------------------------------------------------------------------------
# O1 independent brute-force oracle (never enumerate_starting_hands).
# ---------------------------------------------------------------------------


def _oracle_equity(
    hero: tuple[Card, Card],
    board: tuple[Card, ...],
    opponent_range: Range,
    dead_cards: tuple[Card, ...] = (),
) -> tuple[Fraction, Fraction, Fraction, Fraction, int]:
    """Brute-force (equity, hero_wins, ties, opponent_wins, combo_count)."""
    known = frozenset(hero) | frozenset(board) | frozenset(dead_cards)
    live = [card for card in DECK if card not in known]
    combos = list(itertools.combinations(live, 2))
    total = Fraction(0)
    for combo in combos:
        total += opponent_range.weight(combo)
    hero_eval = _evaluate7(tuple(hero) + tuple(board))
    hero_wins = ties = opponent_wins = Fraction(0)
    for combo in combos:
        outcome = _compare(hero_eval, _evaluate7(tuple(combo) + tuple(board)))
        weight = opponent_range.weight(combo) / total
        if outcome > 0:
            hero_wins += weight
        elif outcome == 0:
            ties += weight
        else:
            opponent_wins += weight
    equity = hero_wins + ties / 2
    return equity, hero_wins, ties, opponent_wins, len(combos)


def _oracle_outcome_counts(
    hero: tuple[Card, Card],
    board: tuple[Card, ...],
    opponent_range: Range,
    dead_cards: tuple[Card, ...] = (),
) -> tuple[int, int]:
    """Brute-force (n_win, n_tie) over live combos, independent of weights."""
    known = frozenset(hero) | frozenset(board) | frozenset(dead_cards)
    live = [card for card in DECK if card not in known]
    hero_eval = _evaluate7(tuple(hero) + tuple(board))
    n_win = n_tie = 0
    for combo in itertools.combinations(live, 2):
        outcome = _compare(hero_eval, _evaluate7(tuple(combo) + tuple(board)))
        if outcome > 0:
            n_win += 1
        elif outcome == 0:
            n_tie += 1
    return n_win, n_tie


# ---------------------------------------------------------------------------
# Positive fixtures (each returns a fresh EquityResult).
# ---------------------------------------------------------------------------

#: Positive-mass live range reused by validation cases (immutable value
#: object; the combo touches no card of the validation fixtures).
_POSITIVE_RANGE = _single_live_range("Qc", "Jc")


def _run_p3() -> EquityResult:
    hero = _pair("2c", "2d")
    board = _cards("As", "Ks", "Qs", "Js", "Ts")
    rng = _single_live_range("7d", "7h")
    return exact_river_equity(hero, board, rng)


def _run_p4() -> EquityResult:
    hero = _pair("Ah", "Kd")
    dead = _dead_excluding(
        "Ah", "Kd", "Qh", "Jh", "Th", "2c", "3c", "Qc", "Jc"
    )
    return exact_river_equity(
        hero, _FLOOD_BOARD, _single_live_range("Qc", "Jc"), dead_cards=dead
    )


def _run_p5() -> EquityResult:
    hero = _pair("Ah", "Kd")
    dead = _dead_excluding(
        "Ah", "Kd", "Qh", "Jh", "Th", "2c", "3c", "Ac", "Kh"
    )
    return exact_river_equity(
        hero, _FLOOD_BOARD, _single_live_range("Ac", "Kh"), dead_cards=dead
    )


def _run_p6() -> EquityResult:
    hero = _pair("2c", "3c")
    dead = _dead_excluding(
        "2c", "3c", "Qh", "Jh", "Th", "2d", "3d", "Qs", "Qc"
    )
    return exact_river_equity(
        hero, _FLOOD_BOARD_D23, _single_live_range("Qs", "Qc"), dead_cards=dead
    )


def _run_p7() -> EquityResult:
    hero = _pair("Ah", "Ad")
    return exact_river_equity(hero, _ACES_BOARD, Range.uniform())


def _run_p8() -> EquityResult:
    hero = _pair("Ah", "Kd")
    rng = (
        Range()
        .set_weight(_pair("Qc", "Jc"), Fraction(1, 6))
        .set_weight(_pair("8d", "8h"), Fraction(1, 3))
        .set_weight(_pair("4d", "4h"), Fraction(1, 2))
        .set_weight(_pair("9c", "8d"), Fraction(1, 2))
        .set_weight(_pair("Ah", "8d"), Fraction(3, 4))
    )
    return exact_river_equity(hero, _FLOOD_BOARD, rng, dead_cards=_cards("9c"))


def _run_p9() -> EquityResult:
    hero = _pair("Ah", "Ad")
    return exact_river_equity(
        hero, _ACES_BOARD, Range.uniform(dead_cards=_cards("9c")),
        dead_cards=_cards("9c"),
    )


def _run_p10() -> EquityResult:
    hero = _pair("Ah", "Kh")
    rng = (
        Range()
        .set_weight(_pair("Kc", "Kd"), Fraction(1, 5))
        .set_weight(_pair("Qc", "Qd"), Fraction(2, 5))
        .set_weight(_pair("Jc", "Jd"), Fraction(1, 10))
        .set_weight(_pair("Ac", "Kc"), Fraction(1, 10))
        .set_weight(_pair("4c", "5c"), Fraction(1, 10))
    )
    return exact_river_equity(hero, _TRIP_K_BOARD, rng)


def _positive_results() -> dict[str, EquityResult]:
    return {
        "P3": _run_p3(),
        "P4": _run_p4(),
        "P5": _run_p5(),
        "P6": _run_p6(),
        "P7": _run_p7(),
        "P8": _run_p8(),
        "P9": _run_p9(),
        "P10": _run_p10(),
    }


class ExactRiverEquityTests(unittest.TestCase):
    """P1-P16 positive/boundary cases (designer handoff IDs preserved)."""

    maxDiff = None

    # -- helpers -----------------------------------------------------------

    def assert_matches_oracle(self, result, hero, board, rng, dead=()):
        equity, wins, ties, losses, count = _oracle_equity(hero, board, rng, dead)
        self.assertEqual(result.equity, equity)
        self.assertEqual(result.hero_wins, wins)
        self.assertEqual(result.ties, ties)
        self.assertEqual(result.opponent_wins, losses)
        self.assertEqual(result.enumerated_opponent_combos, count)

    # -- P-cases -----------------------------------------------------------

    def test_p1_version_and_scope_pins(self):
        self.assertEqual(EQUITY_EXACT_VERSION, "1.0.0")
        self.assertIs(_REEXPORTED_VERSION, EQUITY_EXACT_VERSION)
        self.assertIs(_ReexportedEquityResult, EquityResult)
        self.assertIs(_reexported_function, exact_river_equity)
        doc = (exact_module.__doc__ or "").lower()
        for fragment in (
            "multiway",
            "pre-river",
            "turn",
            "sampling",
            "float",
            "per-combo",
        ):
            self.assertIn(fragment, doc)

    def test_p2_pin_result_shape(self):
        self.assertTrue(dataclasses.is_dataclass(EquityResult))
        self.assertTrue(EquityResult.__dataclass_params__.frozen)
        names = [field.name for field in dataclasses.fields(EquityResult)]
        self.assertEqual(
            names,
            [
                "equity",
                "hero_wins",
                "ties",
                "opponent_wins",
                "enumerated_opponent_combos",
            ],
        )
        result = EquityResult(Fraction(1), Fraction(0), Fraction(0), Fraction(0), 990)
        with self.assertRaises(AttributeError):
            result.equity = Fraction(0)

    def test_p3_royal_board_plays_both(self):
        result = _run_p3()
        self.assertEqual(result.ties, Fraction(1))
        self.assertEqual(result.hero_wins, Fraction(0))
        self.assertEqual(result.opponent_wins, Fraction(0))
        self.assertEqual(result.equity, Fraction(1, 2))
        self.assertEqual(result.enumerated_opponent_combos, 990)
        self.assert_matches_oracle(
            result, _pair("2c", "2d"), _cards("As", "Ks", "Qs", "Js", "Ts"),
            _single_live_range("7d", "7h"),
        )

    def test_p4_single_live_combo_win(self):
        result = _run_p4()
        self.assertEqual(result.equity, Fraction(1))
        self.assertEqual(result.hero_wins, Fraction(1))
        self.assertEqual(result.ties, Fraction(0))
        self.assertEqual(result.opponent_wins, Fraction(0))
        self.assertEqual(result.enumerated_opponent_combos, 1)
        self.assert_matches_oracle(
            result, _pair("Ah", "Kd"), _FLOOD_BOARD,
            _single_live_range("Qc", "Jc"),
            _dead_excluding("Ah", "Kd", "Qh", "Jh", "Th", "2c", "3c", "Qc", "Jc"),
        )

    def test_p5_single_live_combo_tie(self):
        result = _run_p5()
        self.assertEqual(result.ties, Fraction(1))
        self.assertEqual(result.equity, Fraction(1, 2))
        self.assertEqual(result.enumerated_opponent_combos, 1)
        self.assert_matches_oracle(
            result, _pair("Ah", "Kd"), _FLOOD_BOARD,
            _single_live_range("Ac", "Kh"),
            _dead_excluding("Ah", "Kd", "Qh", "Jh", "Th", "2c", "3c", "Ac", "Kh"),
        )

    def test_p6_single_live_combo_loss(self):
        result = _run_p6()
        self.assertEqual(result.opponent_wins, Fraction(1))
        self.assertEqual(result.equity, Fraction(0))
        self.assertEqual(result.enumerated_opponent_combos, 1)
        self.assert_matches_oracle(
            result, _pair("2c", "3c"), _FLOOD_BOARD_D23,
            _single_live_range("Qs", "Qc"),
            _dead_excluding("2c", "3c", "Qh", "Jh", "Th", "2d", "3d", "Qs", "Qc"),
        )

    def test_p7_uniform_range_matches_oracle_and_structural_denominator(self):
        hero = _pair("Ah", "Ad")
        result = _run_p7()
        self.assert_matches_oracle(result, hero, _ACES_BOARD, Range.uniform())
        n_win, n_tie = _oracle_outcome_counts(hero, _ACES_BOARD, Range.uniform())
        self.assertEqual(
            result.equity,
            (Fraction(n_win) + Fraction(n_tie, 2)) * Fraction(1, 990),
        )
        self.assertEqual(result.enumerated_opponent_combos, 990)

    def test_p8_known_combo_weights_contribute_zero(self):
        hero = _pair("Ah", "Kd")
        dead = _cards("9c")
        result = _run_p8()
        self.assert_matches_oracle(result, hero, _FLOOD_BOARD, _run_p8_range(), dead)
        # Live-mass denominator discriminator: the range's global mass is
        # 9/4 (dead-combo weights 1/2 + 3/4 included); a global
        # normalized_weights() denominator would not yield sum(w_norm) == 1.
        self.assertEqual(
            result.hero_wins + result.ties + result.opponent_wins, Fraction(1)
        )

    def test_p9_one_dead_card_count_and_oracle(self):
        hero = _pair("Ah", "Ad")
        dead = _cards("9c")
        result = _run_p9()
        self.assertEqual(result.enumerated_opponent_combos, 946)  # C(44, 2)
        self.assert_matches_oracle(
            result, hero, _ACES_BOARD, Range.uniform(dead_cards=dead), dead
        )

    def test_p10_paired_board_multi_weight_oracle_equality(self):
        hero = _pair("Ah", "Kh")
        result = _run_p10()
        self.assert_matches_oracle(result, hero, _TRIP_K_BOARD, _run_p10_range())

    def test_p11_partition_invariant_over_all_positives(self):
        for name, result in _positive_results().items():
            self.assertEqual(
                result.hero_wins + result.ties + result.opponent_wins,
                Fraction(1),
                name,
            )
            for field_name in ("equity", "hero_wins", "ties", "opponent_wins"):
                value = getattr(result, field_name)
                self.assertGreaterEqual(value, Fraction(0), (name, field_name))
                self.assertLessEqual(value, Fraction(1), (name, field_name))

    def test_p12_tie_split_over_all_positives(self):
        for name, result in _positive_results().items():
            self.assertEqual(
                result.equity,
                result.hero_wins + result.ties / 2,
                name,
            )

    def test_p13_exact_fraction_types(self):
        for name in ("P3", "P7", "P8", "P10"):
            result = _positive_results()[name]
            for field_name in ("equity", "hero_wins", "ties", "opponent_wins"):
                self.assertIs(type(getattr(result, field_name)), Fraction, (name, field_name))
            self.assertIs(type(result.enumerated_opponent_combos), int, name)

    def test_p14_determinism_and_call_order_independence(self):
        first_p7 = _run_p7()
        first_p8 = _run_p8()
        second_p7 = _run_p7()
        second_p8 = _run_p8()
        self.assertEqual(first_p7, second_p7)
        self.assertEqual(first_p8, second_p8)
        self.assertIsNot(first_p7, second_p7)

    def test_p15_range_value_object_purity(self):
        hero = _pair("Ah", "Kd")
        dead = _cards("9c")
        rng = _run_p8_range()
        before_mass = rng.total_mass()
        combo_symbols = (("Qc", "Jc"), ("8d", "8h"), ("4d", "4h"),
                         ("9c", "8d"), ("Ah", "8d"))
        before_weights = [rng.weight(_pair(*symbols)) for symbols in combo_symbols]
        exact_river_equity(hero, _FLOOD_BOARD, rng, dead_cards=dead)
        self.assertEqual(rng.total_mass(), before_mass)
        self.assertEqual(
            [rng.weight(_pair(*symbols)) for symbols in combo_symbols],
            before_weights,
        )

    def test_p16_static_no_sampling_purity(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "src" / "poker" / "equity" / "exact.py"
        ).read_text(encoding="utf-8")
        for fragment in ("import random", "from random", "random."):
            self.assertNotIn(fragment, source)


# Module-level fixture builders referenced above (defined after the class
# body would shadow nothing; placed here for name resolution in methods).
def _run_p8_range() -> Range:
    return (
        Range()
        .set_weight(_pair("Qc", "Jc"), Fraction(1, 6))
        .set_weight(_pair("8d", "8h"), Fraction(1, 3))
        .set_weight(_pair("4d", "4h"), Fraction(1, 2))
        .set_weight(_pair("9c", "8d"), Fraction(1, 2))
        .set_weight(_pair("Ah", "8d"), Fraction(3, 4))
    )


def _run_p10_range() -> Range:
    return (
        Range()
        .set_weight(_pair("Kc", "Kd"), Fraction(1, 5))
        .set_weight(_pair("Qc", "Qd"), Fraction(2, 5))
        .set_weight(_pair("Jc", "Jd"), Fraction(1, 10))
        .set_weight(_pair("Ac", "Kc"), Fraction(1, 10))
        .set_weight(_pair("4c", "5c"), Fraction(1, 10))
    )


class ExactRiverEquityValidationTests(unittest.TestCase):
    """N1-N14 fail-closed validation cases (designer handoff IDs)."""

    maxDiff = None

    _HERO = _pair("Ah", "Kd")

    def test_n1_dead_overlaps_hero(self):
        with self.assertRaises(ValueError):
            exact_river_equity(
                self._HERO, _FLOOD_BOARD, _POSITIVE_RANGE,
                dead_cards=_cards("Ah"),
            )

    def test_n2_dead_overlaps_board(self):
        with self.assertRaises(ValueError):
            exact_river_equity(
                self._HERO, _FLOOD_BOARD, _POSITIVE_RANGE,
                dead_cards=_cards("2c"),
            )

    def test_n3_duplicate_dead_cards(self):
        with self.assertRaises(ValueError):
            exact_river_equity(
                self._HERO, _FLOOD_BOARD, _POSITIVE_RANGE,
                dead_cards=_cards("9c", "9c"),
            )

    def test_n4_non_card_in_dead(self):
        with self.assertRaises(TypeError):
            exact_river_equity(
                self._HERO, _FLOOD_BOARD, _POSITIVE_RANGE,
                dead_cards=("9c",),
            )

    def test_n5_non_card_in_hero(self):
        bad_hero = ("As", _SYMBOL_TO_CARD["Kd"])
        with self.assertRaises(TypeError):
            exact_river_equity(bad_hero, _FLOOD_BOARD, _POSITIVE_RANGE)

    def test_n6_non_card_in_board(self):
        bad_board = _cards("Qh", "Jh", "Th", "2c") + ("3c",)
        with self.assertRaises(TypeError):
            exact_river_equity(self._HERO, bad_board, _POSITIVE_RANGE)

    def test_n7_duplicates_in_hero_and_board(self):
        with self.assertRaises(ValueError):
            exact_river_equity(
                _cards("Ah", "Ah"), _FLOOD_BOARD, _POSITIVE_RANGE
            )
        with self.assertRaises(ValueError):
            exact_river_equity(
                self._HERO, _cards("Kc", "Kc", "7d", "7h", "2s"),
                _POSITIVE_RANGE,
            )

    def test_n8_board_cardinality_loop(self):
        base = _cards("Kc", "Qc", "7d", "7h", "2s")
        variants = {
            4: base[:4],
            3: base[:3],
            6: base + _cards("3d"),
            7: base + _cards("3d", "4d"),
        }
        for size, board in variants.items():
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    exact_river_equity(
                        _pair("Ah", "Ad"), board, _POSITIVE_RANGE
                    )

    def test_n9_hero_cardinality(self):
        with self.assertRaises(ValueError):
            exact_river_equity(_cards("Ah"), _FLOOD_BOARD, _POSITIVE_RANGE)
        with self.assertRaises(ValueError):
            exact_river_equity(
                _cards("Ah", "Kd", "Qc"), _FLOOD_BOARD, _POSITIVE_RANGE
            )

    def test_n10_zero_mass_range(self):
        with self.assertRaises(ValueError):
            exact_river_equity(self._HERO, _FLOOD_BOARD, Range())

    def test_n11_mass_only_on_known_combos(self):
        hero = self._HERO
        dead = _cards("9c")
        # total_mass = 1 > 0, but both weighted combos touch known cards
        # (dead 9c / hero Ah), so the total LIVE mass is exactly 0.
        rng = (
            Range()
            .set_weight(_pair("8d", "9c"), Fraction(1, 2))
            .set_weight(_pair("Ah", "8d"), Fraction(1, 2))
        )
        with self.assertRaisesRegex(
            ValueError, "opponent range has no mass on live combos"
        ):
            exact_river_equity(hero, _FLOOD_BOARD, rng, dead_cards=dead)

    def test_n12_dead_saturation_lattice_empty(self):
        dead = _dead_excluding(
            "Ah", "Kd", "Qh", "Jh", "Th", "2c", "3c", "5c"
        )
        self.assertEqual(len(dead), 44)  # 52 - 7 known - 1 live card
        rng = Range().set_weight(_pair("8d", "8h"), Fraction(1))
        with self.assertRaises(ValueError):
            exact_river_equity(
                self._HERO, _FLOOD_BOARD, rng, dead_cards=dead
            )

    def test_n13_non_range_opponent(self):
        for bad in ({}, "AA"):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    exact_river_equity(
                        self._HERO, _FLOOD_BOARD, bad
                    )

    def test_n14_strict_card_identity_subclass_rejected(self):
        class _SubCard(Card):
            pass

        sub = _SubCard("A", "s")
        with self.assertRaises(TypeError):
            exact_river_equity(
                (sub, _SYMBOL_TO_CARD["Kd"]), _FLOOD_BOARD, _POSITIVE_RANGE
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
