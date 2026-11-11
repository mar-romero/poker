"""Combo-classification tests for POKER-RANGE-CLASSES-001 (cases C01..C31).

Implements the committed test-designer handoff cases verbatim in intent.
Blueprint style mirrors ``tests/range/test_model.py`` /
``tests/range/test_notation.py``: every fixture construction stays inside
test methods so a RED skeleton raises ``NotImplementedError`` at the first
behavioral call with zero collection errors, and all oracles are in-test
definition-driven reimplementations (own canonical 10-window list mirroring
``poker.cards.board``, own singleton/``r*``/``r0`` scans, own per-suit sums
from public ``Card.rank``/``Card.suit``, own recipe order; strength resolves
through the evaluator purely as a black box with the pinned street ladder:
flop ``evaluate`` over the single 5-card set, turn max
``evaluation_key`` over the six ``evaluate`` subsets (never ``evaluate7`` on
6 cards), river ``evaluate7``). No ``classes.py`` internals are imported or
re-called; sweeps read only the public surface
(``classify_range``, ``classify_combo``, ``class_masses``, vocabularies,
``ComboClass``) plus public ``Range``/``combinatorics``/evaluator APIs.

Case map (committed test-designer handoff):
C01..C22 hand-computed per-combo fixtures (OR-1 literals, evaluator
black-box strength OR-3), C23..C25 exact-conservation strip (OR-5,
AC-2), C26..C27 lattice sweeps (OR-4 structural, AC-1 implication strips),
C28 API/surface pin, C29 closed-vocabulary pin, C30/C31 fail-closed matrix
(step-4 negative boundary strip OR-6).

Designer-literal deviations (documented; each one is a case in the
committed test-designer handoff that contradicts the authoritative planner
rule in the same handoff, resolved in favor of the planner AC pin):

- C06 ``overcards``: the case literal claims 0, but designer rule B4 /
  planner AC-5 pin ``count of DISTINCT hole ranks > r0`` with
  ``r0 = r*`` (=4, highest singleton of 9s9d4c2h) and the pocket-nines hole
  rank 9 > 4, so the mechanically pinned value is 1 (the case's own
  parenthetical states r*=4 and hole rank 9 without deriving 0 from any
  rule). Pinned here as 1.
- C17(a) ``backdoor_straight_draw``: the case literal claims 'all draws
  False' for AhKd on QQQ, but designer rule B8 ('flop-only exists
  2-missing window AND no complete window') fires here: the {10..14} window
  is exactly-2-missing ({T,J}) with no complete window over {Q,Q,Q,A,K},
  so the pinned value is True (the planner AC-1 suppression gate is only
  the complete-window test). Pinned here as True.
- C21 ``overcards``: the case literal claims 2, but only the Ace exceeds
  ``r0 = r* = 9`` (hole As,7s on 9s8s2d3c); no consistent alternative r0
  satisfies both this case and C01/C12/C15, so the mechanically pinned
  value is 1. Pinned here as 1.
- C24 construction: the case literal's 'set_weight on uniform(dead=board)
  then total_mass == Fraction(5,7)' is arithmetically impossible
  (``set_weight`` replaces only two entries; the other 1,174 live combos
  keep 1/1176, total 1007/588). The sparse range is therefore built
  from the zero-range constructor with EXACTLY the two set weights
  (AsKs=3/7, TsJs=2/7), total mass exactly 5/7; weight-independence is
  still checked as dict-equality against the uniform(dead=board)
  classification (plus the zero range).
- C23 'total_mass() == Fraction(1,1176)': uniform(dead=Kd7c2h) carries
  weight 1/1176 per live combo over 1,176 live combos, so ``total_mass()``
  is exactly ``Fraction(1)``; the 1/1176 literal is the per-combo weight
  exercised in the per-label cross-check ``Fraction(count, 1176)``.
"""

from __future__ import annotations

import dataclasses
import unittest
from collections import Counter
from fractions import Fraction

from poker.cards.evaluator import HandCategory, compare, evaluate, evaluate7
from poker.domain.cards import DECK, RANKS, Card
from poker.math.combinatorics import enumerate_starting_hands
from poker.range import classes as classes_module
from poker.range.classes import (
    COMBO_CLASSES_VERSION,
    PAIR_CLASS_LABELS,
    SHOWDOWN_LABELS,
    STRENGTH_LABELS,
    ComboClass,
    classify_combo,
    classify_range,
    class_masses,
)
from poker.range.model import Range

# ---------------------------------------------------------------------------
# In-test oracles (never importing or re-calling classes.py internals).
# ---------------------------------------------------------------------------

#: ORACLE rank-strength map, re-derived from the public RANKS order.
_RANK_STRENGTH: dict[str, int] = {rank: index + 2 for index, rank in enumerate(RANKS)}

#: ORACLE canonical 10 straight windows: nine five-strength runs plus the
#: wheel {A,2,3,4,5} counted 5-high (never 14-high) — re-declared locally,
#: mirroring the committed board.py window list from public definitions only.
_ORACLE_WINDOWS: tuple[frozenset[int], ...] = tuple(
    frozenset(range(start, start + 5)) for start in range(2, 11)
) + (frozenset((14, 2, 3, 4, 5)),)

#: Strength label per evaluator category (public ``HandCategory`` names only).
_STRENGTH_BY_CATEGORY: dict[int, str] = {
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

#: Strengths that pin step 1 of the pair-class recipe ('better-than-one-pair').
_BETTER_THAN_ONE_PAIR = frozenset(
    {"two-pair", "trips", "straight", "flush", "full-house", "quads", "straight-flush"}
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


def _oracle_r_star(board) -> int:
    """ORACLE r*: highest singleton board rank strength, sentinel 0."""
    counts = Counter(_RANK_STRENGTH[card.rank] for card in board)
    singletons = sorted(
        (rank for rank, multiplicity in counts.items() if multiplicity == 1),
        reverse=True,
    )
    return singletons[0] if singletons else 0


def _oracle_r0(board) -> int:
    """ORACLE r0 = r* when r* > 0 else the top board rank."""
    counts = Counter(_RANK_STRENGTH[card.rank] for card in board)
    r_star = _oracle_r_star(board)
    return r_star if r_star > 0 else max(counts)


def _oracle_pair_class(board, combo, strength: str) -> str:
    """ORACLE pair class: the pinned AC-5 recipe order, reimplemented."""
    if strength in _BETTER_THAN_ONE_PAIR:
        return "better-than-one-pair"
    hole = [_RANK_STRENGTH[card.rank] for card in combo]
    if hole[0] == hole[1]:
        r = hole[0]
        r_star = _oracle_r_star(board)
        if r > r_star:
            return "overpair"
        if r == r_star:
            return "top-pair"
        return "underpair"
    counts = Counter(_RANK_STRENGTH[card.rank] for card in board)
    paired_singletons = [p for p in hole if counts.get(p, 0) == 1]
    if len(paired_singletons) == 1:
        singletons = sorted(
            (rank for rank, m in counts.items() if m == 1), reverse=True
        )
        if paired_singletons[0] == singletons[0]:
            return "top-pair"
        if len(singletons) > 1 and paired_singletons[0] == singletons[1]:
            return "second-pair"
        return "third-pair-or-lower"
    return "no-made-pair"


def _oracle_overcards(board, combo) -> int:
    """ORACLE overcards: DISTINCT hole ranks strictly greater than r0."""
    r0 = _oracle_r0(board)
    distinct = {_RANK_STRENGTH[card.rank] for card in combo}
    return sum(1 for rank in distinct if rank > r0)


def _oracle_flush_facts(board, combo, n: int) -> tuple[bool, bool, bool, bool]:
    """ORACLE (flush_draw, backdoor_flush, nut_flush, second_nut)."""
    board_suits = Counter(card.suit for card in board)
    combo_suits = Counter(card.suit for card in combo)
    sums = {suit: board_suits.get(suit, 0) + combo_suits.get(suit, 0) for suit in "cdhs"}
    four_flush_suits = [suit for suit, total in sums.items() if total == 4]
    flush_draw = n < 5 and bool(four_flush_suits)
    backdoor_flush = n == 3 and any(total == 3 for total in sums.values())
    nut = flush_draw and any(
        card.suit == four_flush_suits[0] and card.rank == "A" for card in combo
    )
    second = flush_draw and not nut and any(
        card.suit == four_flush_suits[0] and card.rank == "K" for card in combo
    )
    return flush_draw, backdoor_flush, nut, second


def _oracle_straight_facts(board, combo, n: int) -> tuple[bool, bool]:
    """ORACLE (straight_draw, backdoor_straight) with the suppression gate."""
    present = {_RANK_STRENGTH[card.rank] for card in board} | {
        _RANK_STRENGTH[card.rank] for card in combo
    }
    one_missing = False
    two_missing = False
    complete = False
    for window in _ORACLE_WINDOWS:
        missing = 5 - len(window & present)
        if missing == 0:
            complete = True
        elif missing == 1:
            one_missing = True
        elif missing == 2:
            two_missing = True
    straight_draw = n < 5 and one_missing and not complete
    backdoor_straight = n == 3 and two_missing and not complete
    return straight_draw, backdoor_straight


def _oracle_strength_label(board, combo) -> str:
    """ORACLE strength via the evaluator with the pinned street ladder."""
    cards = list(combo) + list(board)
    n = len(board)
    if n == 3:
        best = evaluate(cards)
    elif n == 4:
        best = max(
            (evaluate(subset) for subset in combinations(cards, 5)),
            key=lambda hand: hand.evaluation_key(),
        )
    else:
        best = evaluate7(cards)
    return _STRENGTH_BY_CATEGORY[best.category]


def _oracle_showdown(board, combo) -> str:
    """ORACLE showdown bucket via the river compare (or the off-street label)."""
    if len(board) != 5:
        return "not-showdown-street"
    verdict = compare(evaluate7(list(combo) + list(board)), evaluate(board))
    return "above-board-best" if verdict > 0 else "ties-board-best"


def _oracle_class_tuple(board, combo) -> tuple:
    """Full ORACLE field tuple for one combo (board_size excluded)."""
    n = len(board)
    strength = _oracle_strength_label(board, combo)
    flush_draw, backdoor_flush, nut, second = _oracle_flush_facts(board, combo, n)
    straight_draw, backdoor_straight = _oracle_straight_facts(board, combo, n)
    return (
        strength,
        _oracle_pair_class(board, combo, strength),
        _oracle_overcards(board, combo),
        flush_draw,
        backdoor_flush,
        straight_draw,
        backdoor_straight,
        flush_draw and straight_draw,
        nut,
        second,
        _oracle_showdown(board, combo),
    )


def _combo_class(
    board_size: int,
    strength: str,
    pair_class: str,
    overcards: int,
    *,
    flush: bool = False,
    backdoor_flush: bool = False,
    straight: bool = False,
    backdoor_straight: bool = False,
    combo: bool = False,
    nut: bool = False,
    second: bool = False,
    showdown: str = "not-showdown-street",
) -> ComboClass:
    """Fixture expectation builder: all unpinned booleans default False."""
    return ComboClass(
        board_size=board_size,
        strength=strength,
        pair_class=pair_class,
        overcards=overcards,
        flush_draw=flush,
        backdoor_flush_draw=backdoor_flush,
        straight_draw=straight,
        backdoor_straight_draw=backdoor_straight,
        combo_draw=combo,
        nut_flush_draw=nut,
        second_nut_flush_draw=second,
        showdown_value=showdown,
    )


# ---------------------------------------------------------------------------
# C01..C17, C22 flop fixtures
# ---------------------------------------------------------------------------


class ComboFixtureTests(unittest.TestCase):
    """Hand-computed per-combo literals (OR-1) plus evaluator-black-box OR-3."""

    maxDiff = None

    def _assert_fields(self, actual: ComboClass, **expected) -> None:
        """Assert a selected subset of the classification fields."""
        for name, value in expected.items():
            self.assertEqual(value, getattr(actual, name), name)

    def test_c01_flop_AKs_top_pair_K_rainbow(self) -> None:
        """C01: Kd7c2h + AsKs — pair/top-pair, overcards 1, all draws False."""
        result = classify_combo(_combo("As", "Ks"), _board("Kd", "7c", "2h"))
        self.assertEqual(_combo_class(3, "pair", "top-pair", 1), result)

    def test_c02_flop_solo_combo_nutless_flush_draw(self) -> None:
        """C02: 9s8s2d + TsJs — flush draw + 1-missing straight draw (OESD),
        combo_draw True, nut/second False; backdoor_straight_draw is
        deliberately NOT pinned here (1-missing and 2-missing windows
        co-exist; A1: exclusivity unpinned in 1.0.0)."""
        result = classify_combo(_combo("Ts", "Js"), _board("9s", "8s", "2d"))
        self._assert_fields(
            result,
            board_size=3,
            strength="high-card",
            pair_class="no-made-pair",
            overcards=2,
            flush_draw=True,
            backdoor_flush_draw=False,
            straight_draw=True,
            combo_draw=True,
            nut_flush_draw=False,
            second_nut_flush_draw=False,
            showdown_value="not-showdown-street",
        )

    def test_c03_flop_backdoor_straight_no_draws(self) -> None:
        """C03: 9c2d4s + 5h7d — high-card/no-made-pair, exactly-2-missing
        windows only (backdoor_straight True), no flush family activation."""
        result = classify_combo(_combo("5h", "7d"), _board("9c", "2d", "4s"))
        self.assertEqual(
            _combo_class(3, "high-card", "no-made-pair", 0, backdoor_straight=True),
            result,
        )

    def test_c07_flop_made_straight_gate(self) -> None:
        """C07: 2d3c4h + 5s6d — complete {2..6} window suppresses both
        straight labels; flush family all False (max suit sum 2)."""
        result = classify_combo(_combo("5s", "6d"), _board("2d", "3c", "4h"))
        self.assertEqual(
            _combo_class(3, "straight", "better-than-one-pair", 2), result
        )

    def test_c08_flop_made_straight_flush_monster(self) -> None:
        """C08: 5s6s7s + 4s8s — straight-flush; every draw label False
        (flush made sum 5; complete 4..8 window suppression)."""
        result = classify_combo(_combo("4s", "8s"), _board("5s", "6s", "7s"))
        self.assertEqual(
            _combo_class(3, "straight-flush", "better-than-one-pair", 1), result
        )

    def test_c09_flop_low_sentinel_r_star_overcards(self) -> None:
        """C09: 9c9d2h + 9sAh — trips; r*=2 sentinel-low => both distinct
        hole ranks (9, A) count as overcards per the pinned r0 rule."""
        result = classify_combo(_combo("9s", "Ah"), _board("9c", "9d", "2h"))
        self.assertEqual(
            _combo_class(3, "trips", "better-than-one-pair", 2), result
        )

    def test_c10_flop_two_pair_higher_hole_excluded(self) -> None:
        """C10: 7d7c2h + 2sAs — two-pair; hole deuce is NOT > r0 = 2, so
        overcards counts only the Ace."""
        result = classify_combo(_combo("2s", "As"), _board("7d", "7c", "2h"))
        self.assertEqual(
            _combo_class(3, "two-pair", "better-than-one-pair", 1), result
        )

    def test_c11_flop_underpair_pocket_fours(self) -> None:
        """C11: Kd7c2h + 4d4s — pocket pair below r* = 13 => 'underpair'."""
        result = classify_combo(_combo("4d", "4s"), _board("Kd", "7c", "2h"))
        self.assertEqual(_combo_class(3, "pair", "underpair", 0), result)

    def test_c12_flop_overpair_pocket_aces(self) -> None:
        """C12: Kd7c2h + AsAd — pocket 14 > r* = 13 => 'overpair'."""
        result = classify_combo(_combo("As", "Ad"), _board("Kd", "7c", "2h"))
        self.assertEqual(_combo_class(3, "pair", "overpair", 1), result)

    def test_c13_flop_top_pair_unpaired_board(self) -> None:
        """C13: Kc7h2d + KdAh — step-3 top-pair (p = 13 = r*)."""
        result = classify_combo(_combo("Kd", "Ah"), _board("Kc", "7h", "2d"))
        self.assertEqual(_combo_class(3, "pair", "top-pair", 1), result)

    def test_c14_flop_second_pair(self) -> None:
        """C14: Kd7c2s + 7dAh — step-3 second-pair (p = 7 = second singleton)."""
        result = classify_combo(_combo("7d", "Ah"), _board("Kd", "7c", "2s"))
        self.assertEqual(_combo_class(3, "pair", "second-pair", 1), result)

    def test_c15_flop_third_pair(self) -> None:
        """C15: Kd7c2s + 2cAh — step-3 third-pair-or-lower (p = 2, bottom)."""
        result = classify_combo(_combo("2c", "Ah"), _board("Kd", "7c", "2s"))
        self.assertEqual(
            _combo_class(3, "pair", "third-pair-or-lower", 1), result
        )

    def test_c16_flop_backdoor_only(self) -> None:
        """C16: Kd7c2h + Ts9d — high-card/no-made-pair/0 overcards with a
        exactly-2-missing {J,Q} broadway window => backdoor True."""
        result = classify_combo(_combo("Ts", "9d"), _board("Kd", "7c", "2h"))
        self.assertEqual(
            _combo_class(3, "high-card", "no-made-pair", 0, backdoor_straight=True),
            result,
        )

    def test_c17_flop_trips_board_sentinel(self) -> None:
        """C17: QhQsQd — r* sentinel 0; r0 fallback = top board rank Q.
        (a) AhKd: trips, overcards 2, backdoor via the exactly-2-missing
        {T..A} window (documented deviation). (b) AcAd: full-house,
        overcards 1 (Ace only > r0 = 12)."""
        board = _board("Qh", "Qs", "Qd")
        result_a = classify_combo(_combo("Ah", "Kd"), board)
        self.assertEqual(
            _combo_class(3, "trips", "better-than-one-pair", 2, backdoor_straight=True),
            result_a,
        )
        result_b = classify_combo(_combo("Ac", "Ad"), board)
        self.assertEqual(
            _combo_class(3, "full-house", "better-than-one-pair", 1), result_b
        )

    def test_c22_flop_nut_and_second_nut_exclusive(self) -> None:
        """C22: 9s8s2d — (a) AsKs holds BOTH suit A and K => nut True,
        second False (mutual exclusivity); (b) KsQs => nut False, second
        True; both strength 'high-card', 'no-made-pair', 2 overcards.
        straight_draw stays False for both; (b) backdoor_straight_draw is
        pinned True by the mechanical B8 rule ({T,J} exactly-2-missing in
        the {9..K} window, no complete window) — the case literal's '(both)
        straight family False' is a sixth documented deviation resolved
        toward the planner pin."""
        board = _board("9s", "8s", "2d")
        result_a = classify_combo(_combo("As", "Ks"), board)
        self.assertEqual(
            _combo_class(
                3, "high-card", "no-made-pair", 2, flush=True, nut=True
            ),
            result_a,
        )
        result_b = classify_combo(_combo("Ks", "Qs"), board)
        self.assertEqual(
            _combo_class(
                3,
                "high-card",
                "no-made-pair",
                2,
                flush=True,
                second=True,
                backdoor_straight=True,
            ),
            result_b,
        )


# ---------------------------------------------------------------------------
# C04..C06, C21 turn fixtures
# ---------------------------------------------------------------------------


class TurnFixtureTests(unittest.TestCase):
    """Turn fixtures (n=4): six-subset strength ladder + street gates."""

    def test_c04_turn_made_flush_coexistence_edge(self) -> None:
        """C04: 9s8sTs2s + As7h — made 5-flush: strength 'flush', all flush
        draw labels False by arithmetic exclusion (even with the hole Ace
        of suit; nut implies flush_draw), 1-missing {6..T} window keeps
        straight_draw True. combo_draw is pinned False by the authoritative
        AC-1 definition (combo_draw := flush_draw AND straight_draw, and
        flush_draw is False on the made flush); the case literal's
        'combo_draw True' is a fifth documented deviation resolved toward
        the planner pin."""

        result = classify_combo(_combo("As", "7h"), _board("9s", "8s", "Ts", "2s"))
        self.assertEqual(
            _combo_class(4, "flush", "better-than-one-pair", 1, straight=True),
            result,
        )

    def test_c05_turn_made_straight_suppression_pin(self) -> None:
        """C05: 3s4d5c6h + 7d2c — THE suppression pin: strength 'straight'
        while the 8-high straight is one card away; both straight draw
        labels False because the {3..7} window is complete."""
        result = classify_combo(_combo("7d", "2c"), _board("3s", "4d", "5c", "6h"))
        self.assertEqual(
            _combo_class(4, "straight", "better-than-one-pair", 1), result
        )

    def test_c06_turn_pocket_quads(self) -> None:
        """C06: 9s9d4c2h + 9c9h — quads via the SIX 5-card subsets
        ('evaluate7' must never run on turn); overcards mechanically 1
        (documented deviation from the case literal's 0)."""
        result = classify_combo(_combo("9c", "9h"), _board("9s", "9d", "4c", "2h"))
        self.assertEqual(
            _combo_class(4, "quads", "better-than-one-pair", 1), result
        )

    def test_c21_turn_nut_flush_draw(self) -> None:
        """C21: 9s8s2d3c + As7s — flush_draw True, nut True (hole suit
        Ace), second False; no 1-missing window => straight/combo False;
        overcards mechanically 1 (documented deviation from literal 2)."""
        result = classify_combo(_combo("As", "7s"), _board("9s", "8s", "2d", "3c"))
        self.assertEqual(
            _combo_class(4, "high-card", "no-made-pair", 1, flush=True, nut=True),
            result,
        )


# ---------------------------------------------------------------------------
# C18..C20 river fixtures
# ---------------------------------------------------------------------------


class RiverFixtureTests(unittest.TestCase):
    """River fixtures (n=5): evaluate7 strength + showdown buckets + AC-3."""

    def test_c18_river_full_house_above_board(self) -> None:
        """C18: 2c2d9h7sKc + 9s9d — 999/22 full house beats the board pair;
        ALL six draw/nut booleans False on river (AC-3)."""
        result = classify_combo(_combo("9s", "9d"), _board("2c", "2d", "9h", "7s", "Kc"))
        self.assertEqual(
            _combo_class(
                5, "full-house", "better-than-one-pair", 0, showdown="above-board-best"
            ),
            result,
        )

    def test_c19_river_board_straight_ties(self) -> None:
        """C19: 9h8h7d6s5c + 2c2d — hole-inclusive max TIES the board-only
        straight (never dominates) => 'ties-board-best'; all six booleans
        False (AC-3)."""
        result = classify_combo(_combo("2c", "2d"), _board("9h", "8h", "7d", "6s", "5c"))
        self.assertEqual(
            _combo_class(
                5, "straight", "better-than-one-pair", 0, showdown="ties-board-best"
            ),
            result,
        )

    def test_c20_river_high_card_kicker_improvement(self) -> None:
        """C20: 2c3d4h6s8c + Kd9s — K-high beats the 8-high board =>
        'above-board-best'; straight_draw False DESPITE the exactly-1-missing
        {2..6} window (river street gate, AC-3)."""
        result = classify_combo(_combo("Kd", "9s"), _board("2c", "3d", "4h", "6s", "8c"))
        self.assertEqual(
            _combo_class(
                5, "high-card", "no-made-pair", 2, showdown="above-board-best"
            ),
            result,
        )


# ---------------------------------------------------------------------------
# C23..C25 exact-mass conservation strip (AC-2)
# ---------------------------------------------------------------------------


class MassConservationTests(unittest.TestCase):
    """class_masses exact-Fraction conservation and purity strip."""

    def test_c23_uniform_conservation_and_cross_check(self) -> None:
        """C23: uniform(dead=Kd7c2h) — three zero-filled families keyed
        exactly by the vocabularies; every label mass == Fraction(count,
        1176) via classify_range counts; per-family sums == total_mass ==
        Fraction(1); the flop showdown family is the full mass."""
        board = _board("Kd", "7c", "2h")
        rng = Range.uniform(dead_cards=board)
        masses = class_masses(rng, board)
        self.assertEqual(
            {"strength", "pair_class", "showdown_value"}, set(masses)
        )
        total = rng.total_mass()
        self.assertEqual(Fraction(1), total)
        classification = classify_range(rng, board)
        self.assertEqual(1176, len(classification))
        for family, labels in (
            ("strength", STRENGTH_LABELS),
            ("pair_class", PAIR_CLASS_LABELS),
            ("showdown_value", SHOWDOWN_LABELS),
        ):
            self.assertEqual(set(labels), set(masses[family]), family)
            family_sum = sum(masses[family].values(), Fraction(0))
            self.assertEqual(total, family_sum, family)
            for label in labels:
                count = sum(
                    1
                    for cc in classification.values()
                    if getattr(cc, family) == label
                )
                self.assertEqual(
                    Fraction(count, 1176),
                    masses[family][label],
                    f"{family}/{label}",
                )
        self.assertEqual(
            total, masses["showdown_value"]["not-showdown-street"]
        )

    def test_c24_sparse_conservation_and_weight_independence(self) -> None:
        """C24: sparse range (AsKs=3/7, TsJs=2/7) — per-family sums ==
        total_mass == Fraction(5,7) exactly; classify_range is
        weight-independent: equal dicts for uniform, sparse AND the zero
        range."""
        board = _board("Kd", "7c", "2h")
        uniform_rng = Range.uniform(dead_cards=board)
        sparse = (
            Range()
            .set_weight(_combo("As", "Ks"), Fraction(3, 7))
            .set_weight(_combo("Ts", "Js"), Fraction(2, 7))
        )
        self.assertEqual(Fraction(5, 7), sparse.total_mass())
        masses = class_masses(sparse, board)
        for family in masses:
            self.assertEqual(
                sparse.total_mass(), sum(masses[family].values(), Fraction(0))
            )
        uniform_classification = classify_range(uniform_rng, board)
        self.assertEqual(uniform_classification, classify_range(sparse, board))
        self.assertEqual(uniform_classification, classify_range(Range(), board))

    def test_c25_zero_range_masses(self) -> None:
        """C25: zero range — every family value Fraction(0), sums == 0 ==
        total_mass, raising nothing."""
        board = _board("Kd", "7c", "2h")
        zero_rng = Range.uniform(dead_cards=board).condition(
            tuple(card for card in DECK if card not in board)
        )
        self.assertEqual(Fraction(0), zero_rng.total_mass())
        masses = class_masses(zero_rng, board)
        self.assertEqual(
            {"strength", "pair_class", "showdown_value"}, set(masses)
        )
        for family, labels in (
            ("strength", STRENGTH_LABELS),
            ("pair_class", PAIR_CLASS_LABELS),
            ("showdown_value", SHOWDOWN_LABELS),
        ):
            self.assertEqual(set(labels), set(masses[family]), family)
            family_sum = sum(masses[family].values(), Fraction(0))
            self.assertEqual(Fraction(0), family_sum, family)
            self.assertEqual(zero_rng.total_mass(), family_sum, family)
        # Zero weights must not distort the classification partition itself.
        self.assertEqual(
            classify_range(Range(), board), classify_range(zero_rng, board)
        )


# ---------------------------------------------------------------------------
# C26..C27 lattice sweeps (OR-4 structural + AC-1 implication strips)
# ---------------------------------------------------------------------------


class RangeSweepTests(unittest.TestCase):
    """classify_range sweeps: lattice keys, determinism, freeze, invariants."""

    maxDiff = None

    def test_c26_flop_sweep(self) -> None:
        """C26: full flop sweep over Kd7c2h — 1176 keys == the public
        lattice, every value a frozen ComboClass (board_size 3, labels in
        vocabulary), two calls equal, AC-1 implications hold, and every
        field matches the in-test definition-driven oracle."""
        board = _board("Kd", "7c", "2h")
        expected_keys = set(enumerate_starting_hands(dead_cards=board))
        result = classify_range(Range.uniform(dead_cards=board), board)
        self.assertEqual(1176, len(result))
        self.assertEqual(expected_keys, set(result))
        self.assertEqual(result, classify_range(Range.uniform(dead_cards=board), board))
        frozen = next(iter(result.values()))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            frozen.strength = "high-card"
        for combo, cc in result.items():
            self.assertTrue(
                isinstance(cc, ComboClass) and dataclasses.is_dataclass(cc),
                f"{combo[0].symbol}{combo[1].symbol}",
            )
            self.assertEqual(3, cc.board_size)
            self.assertIn(cc.strength, STRENGTH_LABELS)
            self.assertIn(cc.pair_class, PAIR_CLASS_LABELS)
            self.assertIn(cc.showdown_value, SHOWDOWN_LABELS)
            self.assertEqual(
                cc.combo_draw, cc.flush_draw and cc.straight_draw, str(combo)
            )
            self.assertFalse(
                cc.nut_flush_draw and cc.second_nut_flush_draw, str(combo)
            )
            if cc.nut_flush_draw or cc.second_nut_flush_draw:
                self.assertTrue(cc.flush_draw, str(combo))
            self.assertFalse(
                cc.flush_draw and cc.backdoor_flush_draw, str(combo)
            )
        for combo, cc in result.items():
            self.assertEqual(
                _oracle_class_tuple(board, combo),
                (
                    cc.strength,
                    cc.pair_class,
                    cc.overcards,
                    cc.flush_draw,
                    cc.backdoor_flush_draw,
                    cc.straight_draw,
                    cc.backdoor_straight_draw,
                    cc.combo_draw,
                    cc.nut_flush_draw,
                    cc.second_nut_flush_draw,
                    cc.showdown_value,
                ),
                f"{combo[0].symbol}{combo[1].symbol}",
            )

    def test_c27_turn_river_sweeps(self) -> None:
        """C27: turn sweep len 1128 == C(48,2) over 9s8s2d3c and river
        sweep len 1081 == C(47,2) over 2c3d4h6s8c; keys == lattice sets;
        labels in the vocabularies; river rows carry ONLY the two river
        showdown labels (never 'not-showdown-street') and ALL six draw/nut
        booleans are False (AC-3); turn backdoor labels are False (AC-3)."""
        turn_board = _board("9s", "8s", "2d", "3c")
        turn_result = classify_range(Range.uniform(dead_cards=turn_board), turn_board)
        self.assertEqual(1128, len(turn_result))
        self.assertEqual(
            set(enumerate_starting_hands(dead_cards=turn_board)), set(turn_result)
        )
        for combo, cc in turn_result.items():
            self.assertEqual(4, cc.board_size, str(combo))
            self.assertEqual("not-showdown-street", cc.showdown_value, str(combo))
            self.assertIn(cc.strength, STRENGTH_LABELS)
            self.assertIn(cc.pair_class, PAIR_CLASS_LABELS)
            self.assertFalse(cc.backdoor_flush_draw, str(combo))
            self.assertFalse(cc.backdoor_straight_draw, str(combo))
        river_board = _board("2c", "3d", "4h", "6s", "8c")
        river_result = classify_range(Range.uniform(dead_cards=river_board), river_board)
        self.assertEqual(1081, len(river_result))
        self.assertEqual(
            set(enumerate_starting_hands(dead_cards=river_board)), set(river_result)
        )
        six_draw_flags = (
            "flush_draw",
            "backdoor_flush_draw",
            "straight_draw",
            "backdoor_straight_draw",
            "combo_draw",
            "nut_flush_draw",
            "second_nut_flush_draw",
        )
        for combo, cc in river_result.items():
            self.assertEqual(5, cc.board_size, str(combo))
            self.assertIn(
                cc.showdown_value, ("above-board-best", "ties-board-best"), str(combo)
            )
            self.assertIn(cc.strength, STRENGTH_LABELS)
            self.assertIn(cc.pair_class, PAIR_CLASS_LABELS)
            for flag in six_draw_flags:
                self.assertFalse(getattr(cc, flag), f"{flag} on {combo}")


# ---------------------------------------------------------------------------
# C28..C29 surface/vocabulary pins
# ---------------------------------------------------------------------------


class SurfacePinTests(unittest.TestCase):
    """AC-4 minimal API surface and AC-5 closed vocabulary pin."""

    def test_c28_api_surface_pin(self) -> None:
        """C28: version '1.0.0'; __all__ exactly the five pinned names;
        ComboClass frozen dataclass with EXACTLY the 12 pinned fields in
        order — verified through a REAL classify_combo C01 result so the
        RED skeleton fails with NotImplementedError rather than silently."""
        self.assertEqual("1.0.0", COMBO_CLASSES_VERSION)
        self.assertEqual(
            {
                "COMBO_CLASSES_VERSION",
                "ComboClass",
                "classify_combo",
                "classify_range",
                "class_masses",
            },
            set(classes_module.__all__),
        )
        field_names = [field.name for field in dataclasses.fields(ComboClass)]
        self.assertEqual(
            [
                "board_size",
                "strength",
                "pair_class",
                "overcards",
                "flush_draw",
                "backdoor_flush_draw",
                "straight_draw",
                "backdoor_straight_draw",
                "combo_draw",
                "nut_flush_draw",
                "second_nut_flush_draw",
                "showdown_value",
            ],
            field_names,
        )
        result = classify_combo(_combo("As", "Ks"), _board("Kd", "7c", "2h"))
        self.assertIsInstance(result, ComboClass)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.board_size = 4

    def test_c29_closed_vocabularies(self) -> None:
        """C29: the three vocabulary tuples match the pinned literals in
        length AND membership ('strength' 9, 'pair_class' 7, 'showdown' 3);
        a live classify_range sweep emits only vocabulary members."""
        self.assertEqual(
            (
                "high-card",
                "pair",
                "two-pair",
                "trips",
                "straight",
                "flush",
                "full-house",
                "quads",
                "straight-flush",
            ),
            STRENGTH_LABELS,
        )
        self.assertEqual(
            (
                "better-than-one-pair",
                "overpair",
                "top-pair",
                "second-pair",
                "third-pair-or-lower",
                "underpair",
                "no-made-pair",
            ),
            PAIR_CLASS_LABELS,
        )
        self.assertEqual(
            ("above-board-best", "ties-board-best", "not-showdown-street"),
            SHOWDOWN_LABELS,
        )
        board = _board("9s", "8s", "2d")
        result = classify_range(Range.uniform(dead_cards=board), board)
        self.assertEqual(1176, len(result))
        for cc in result.values():
            self.assertIn(cc.strength, STRENGTH_LABELS)
            self.assertIn(cc.pair_class, PAIR_CLASS_LABELS)
            self.assertIn(cc.showdown_value, SHOWDOWN_LABELS)


# ---------------------------------------------------------------------------
# C30..C31 fail-closed matrix (STEP-4 negative boundary strip)
# ---------------------------------------------------------------------------


class FailClosedTests(unittest.TestCase):
    """Exception-type pins only (messages unpinned per designer A3)."""

    def test_c30_combo_negatives(self) -> None:
        """C30: arity 1/3 -> TypeError; duplicate card inside the combo ->
        ValueError; combo card on the board -> ValueError; str symbols ->
        TypeError; Card-subclass instances -> TypeError (strict type()
        identity)."""
        board = _board("Kd", "7c", "2h")
        with self.assertRaises(TypeError):
            classify_combo(_cards("As"), board)
        with self.assertRaises(TypeError):
            classify_combo(_cards("As", "Ks", "Qd"), board)
        with self.assertRaises(ValueError):
            classify_combo(_cards("As", "As"), board)
        with self.assertRaises(ValueError):
            classify_combo(_cards("Kd", "As"), board)
        with self.assertRaises(TypeError):
            classify_combo(("As", "Ks"), board)

        class _CardSubclass(Card):
            pass

        with self.assertRaises(TypeError):
            classify_combo(
                (_CardSubclass("A", "s"), _CardSubclass("K", "s")), board
            )

    def test_c31_board_and_range_negatives(self) -> None:
        """C31: board size 2/6 -> ValueError; duplicate board card ->
        ValueError; str-card board -> TypeError; non-Range (str / object)
        first argument -> TypeError; valid Range with a 2-card board ->
        ValueError (both range APIs)."""
        board = _board("Kd", "7c", "2h")
        combo = _combo("As", "Ks")
        with self.assertRaises(ValueError):
            classify_combo(combo, tuple(_cards("Kd", "7c")))
        with self.assertRaises(ValueError):
            classify_combo(combo, tuple(_cards("Kd", "7c", "2h", "As", "Ks", "Qd")))
        with self.assertRaises(ValueError):
            classify_combo(combo, _board("Kd", "7c", "Kd"))
        with self.assertRaises(ValueError):
            classify_combo(combo, _board("Kd", "7c", "Kd"))
        with self.assertRaises(TypeError):
            classify_combo(combo, ["Kd", "7c", "2h"])
        with self.assertRaises(TypeError):
            classify_range("x", board)
        with self.assertRaises(TypeError):
            class_masses(object(), board)
        with self.assertRaises(ValueError):
            classify_range(Range.uniform(), tuple(_cards("Kd", "7c")))
        with self.assertRaises(ValueError):
            class_masses(Range.uniform(), tuple(_cards("Kd", "7c")))


if __name__ == "__main__":
    unittest.main()
