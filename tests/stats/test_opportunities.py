"""Behavior-defining tests for the generic opportunity evaluator (R2, TDD).

Two-phase lazy-import RED design (mirrors tests/features/test_materialize.py):
there are NO module-level imports of ``poker.stats``; ``setUp`` reaches the
stats layer through ``importlib.import_module`` calls. Phase A therefore fails
per test with ``ModuleNotFoundError: No module named 'poker.stats'`` (zero
collection errors); Phase B fails behaviorally at ``NotImplementedError``;
GREEN provides the real modules.

Designer oracle (test-designer.json, task POKER-STATS-OPPORTUNITY-001):
K01-K03 structure, D01-D05 definitions contract, S01-S05 steal, B01-B05
BB defense, T01-T03 three bet, F01-F03 fold-to-three-bet, C01-C03 c-bet,
F10-F11 fold-to-cbet, X01-X03 D3 exclusions, W01-W03 heads-up/re-opened,
Z01-Z03 zero-opportunity/determinism/purity, B01X-B03X boundaries. Every
expected successes/opportunities pair is hand-derived from planner D2/D3
wording and asserted exactly; fixtures are replay-legality-verified against
``StateEngine.replay`` before totals are asserted (fixture bugs fix the
fixture, never the rule).

Documented resolution notes (designer unresolveds, resolved from planner
D1-D8 wording only; never inventing vocabulary):

* U1 (correspondence fail-closed): ``decided_action(snapshot, actions)`` is
  the pure helper carrying the D8 check (snapshot.action_index + 1 must index
  a real, non-POST action by the snapshot actor, else ValueError); tested
  directly with synthetic mismatched pairs. The public ``evaluate_hand`` API
  replays internally, so the misalignment is not triggerable through it —
  the check stays defensive and the public API is not weakened.
* U2 (reachability of all-in/fold-only acting snapshots): under strict
  replay, no acting snapshot with ``effective_stack[actor] == 0`` or a
  fold-only legal set occurred in any of the 11 replay-verified golden
  fixtures; D3 rules (ii)/(iii) negatives are therefore pinned at the
  pure-helper level (X01, per D3 "tested with positive and negative cases"),
  while integration exclusion coverage rests on X02/X03.
* U3 (G-FAI legal set): observed by replay = exactly ``{FOLD, CALL}`` for
  BB's facing-all-in decision (engine probe, no RAISE — the shove exceeds
  BB's remaining stack); X02 asserts the facing-all-in-kept totals; the
  fold-only fallback branch is not needed and stays covered at helper level.
* U4 (<2 active ValueError): ``is_heads_up(snapshot)`` is the D4 helper;
  W03 pins the defensive ValueError at the helper level (unreachable via
  strict replay).
* U5 (heads_up representation): dedicated bool field on OpportunityMatch
  (D8 wins over D2's "heads_up as dimension" phrasing); ``match.dimensions``
  carries only position/opener_position; the name ``heads_up`` stays inside
  ``StatDefinition.dimensions`` as the dimension name consumed by downstream
  aggregation (POKER-STATS-PREFLOP/POSTFLOP/DSL/BAYES).

Oracle corrections (hand-re-derived from planner D2/D3 after
replay-legality-verified evaluation; documented per the evolution-check
rule, never silently changed):

* G-H1/T01: the designer hand-asserted ``three_bet(1,2)`` with "BB decision
  success=True". Per D2, three_bet success = decided RAISE; in G-H1 SB
  decided FOLD and BB decided CALL, so the correct pair is ``three_bet(0,2)``.
  Corroborated by the designer's own table: G-H13 has the identical preflop
  shape and asserts three_bet(0,2), and G-H6/G-H7 show folds/calls are never
  three_bet successes ((0,1) and (0,3)). Mutation R01 is still caught (no
  three_bet match at BTN's opening action_index, indices stay {3, 4}).
* G-H9/S03: the designer hand-asserted ``steal(1,1)``. BTN's unopened FOLD
  decision (rc == 0, cc == 0, position BTN) is ALSO a steal attempt by the
  designer's own G-H2 pin ("HU BTN open IS a steal attempt", succeeded=
  False), so the D2-derived pair is ``steal(1,2)``: SB's open succeeds,
  BTN's fold does not. Mutation R08/R09 coverage is unchanged.
* G-H1/F01 and G-H13/C03: the designer hand-asserted ``fold_to_three_bet
  (1,1)``. BB's preflop decision (rc == 1, cc == 0, decided CALL) is an
  identical fold_to_three_bet OPPORTUNITY per D2 (only ``succeeded``
  differs: CALL is not FOLD), and the designer's own G-H6/G-H11 count
  decided CALL/RAISE decisions as fold_to_three_bet opportunities ((0,1)
  each), so the D2-derived pair is ``(1,2)`` in both hands. The G-H1 SB
  match (success, opener BTN) and the G-H6/G-H7 pairs are unchanged.
* G-H12/W01 (same root cause, boolean flag not a pair): the designer's note
  "preflop matches (3 active) heads_up=False" overlooks that SB folded
  before BB's preflop decision. Per D4, heads_up is evaluated AT the decision
  point, so BB's three_bet/fold_to_three_bet/bb_defense decision in G-H1/
  G-H12 carries heads_up=True (2 active: Btn, Bb — engine-verified). The W01
  flip proof is unchanged: BTN's preflop steal match is heads_up=False
  (3 active), the flop cbet / fold_to_cbet matches are heads_up=True
  (2 active), and G-H2's HU match is heads_up=True.
* G-H6 fixture turn order: the designer's sequence "BB RAISE to90, SB FOLD,
  BTN FOLD" is not engine-legal (after BB acts, BTN is next clockwise;
  WrongTurnError observed). Fixed to "BB RAISE to90, BTN FOLD, SB FOLD" —
  a fixture bug, never a rule change; all hand-asserted totals unchanged.
* F10 dimension pin: fold_to_cbet matches carry ``opener_position`` keyed to
  the preflop final aggressor's position (test-designer F10), which extends
  the planner D2 dimensions list ((position, heads_up) for fold_to_cbet);
  the designer pin is honored, documented in definitions.py, and changes no
  pair value.
* C01/B01 heads_up flags follow the same D4-at-decision-point rule as the
  W01 note (BB's decision after SB folds: 2 active => heads_up=True), so the
  designer's "heads_up=False" notes on those two cases are corrected to the
  D4-derived values; the (successes, opportunities) pairs are untouched.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import os
import re
import unittest

from poker.domain.game import (
    Action,
    ActionKind,
    AmountSemantics,
    BlindAnteConfig,
    Player,
    Seat,
    Street,
    TableMetadata,
)
from poker.domain.money import Chips
from poker.engine.state import (
    SCHEMA_VERSION,
    STATE_VERSION,
    DecisionSnapshot,
    StateEngine,
)
from tests.domain.test_pots import _check, _fold, _post, _total_to

_PROV = "test:stats:opportunities"


# ---------------------------------------------------------------------------
# Action builders (canonical game.Action instances; _post/_total_to/_fold/
# _check shared from tests.domain.test_pots per the test_state_machine pin)
# ---------------------------------------------------------------------------


def _call_total(actor, total, seq, street=Street.PREFLOP):
    return Action(
        actor, street, ActionKind.CALL, Chips(total), AmountSemantics.TOTAL_TO, seq, _PROV
    )


def _bet_added(actor, amount, seq, street=Street.FLOP):
    return Action(
        actor, street, ActionKind.BET, Chips(amount), AmountSemantics.ADDED, seq, _PROV
    )


# ---------------------------------------------------------------------------
# Golden fixtures (all engine-legal; replay-verified before totals asserted)
# ---------------------------------------------------------------------------


def _make_table(names, stacks, sb=5, bb=10):
    """3-max: seats 2=BTN, 3=SB, 4=BB; 4-max adds seat 5=UTG; HU: seats 2/3."""
    players = tuple(Player(name, Chips(stack)) for name, stack in zip(names, stacks))
    seats = tuple(Seat(2 + index) for index in range(len(names)))
    blinds = BlindAnteConfig(Chips(sb), Chips(bb), Chips(0))
    return TableMetadata(players, seats, blinds)


_P3 = ("Btn", "Sb", "Bb")
_P4 = ("Btn", "Sb", "Bb", "Utg")
_HU = ("Btn", "Bb")
_POSTS3 = (_post("Sb", 5, 0), _post("Bb", 10, 1))
_POSTS4 = (_post("Sb", 5, 0), _post("Bb", 10, 1))
_POSTS_HU = (_post("Btn", 5, 0), _post("Bb", 10, 1))


def _fixtures():
    return {
        # G-H1 [3max, stacks 200, sb5/bb10]: BTN open, SB fold, BB call;
        # flop: BB check, BTN c-bet 10, BB fold. Positive golden for all six.
        "G-H1": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _total_to("Btn", 30, 2),
                _fold("Sb", 3),
                _call_total("Bb", 30, 4),
                _check("Bb", 5, Street.FLOP),
                _bet_added("Btn", 10, 6),
                _fold("Bb", 7, Street.FLOP),
            ),
        ),
        # G-H2 [HU]: BTN open... BTN FOLD (HU BTN open IS a steal attempt pin).
        "G-H2": (_make_table(_HU, (200, 200)), 2, _POSTS_HU + (_fold("Btn", 2),)),
        # G-H3 [3max]: BTN limp, SB limp, BB check; flop: SB check, BB donk
        # bet 10, BTN fold (no preflop raise => no c-bet anywhere).
        "G-H3": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _call_total("Btn", 10, 2),
                _call_total("Sb", 10, 3),
                _check("Bb", 4),
                _check("Sb", 5, Street.FLOP),
                _bet_added("Bb", 10, 6),
                _fold("Btn", 7, Street.FLOP),
            ),
        ),
        # G-H4 [3max]: BTN limp, SB limp, BB iso-raise to30, BTN fold,
        # SB fold (raise over limpers is NOT a 3bet: call_count==1 pin).
        "G-H4": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _call_total("Btn", 10, 2),
                _call_total("Sb", 10, 3),
                _total_to("Bb", 30, 4),
                _fold("Btn", 5),
                _fold("Sb", 6),
            ),
        ),
        # G-H6 [3max]: BTN open, SB call, BB raise to90, BTN fold (re-opened),
        # SB fold (re-opened). Fixture turn order corrected: after BB acts,
        # BTN is next clockwise (designer's SB-then-BTN folds raised
        # WrongTurnError; totals unchanged).
        "G-H6": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _total_to("Btn", 30, 2),
                _call_total("Sb", 30, 3),
                _total_to("Bb", 90, 4),
                _fold("Btn", 5),
                _fold("Sb", 6),
            ),
        ),
        # G-H7 [4max]: UTG open, everyone folds (UTG open is NOT a steal).
        "G-H7": (
            _make_table(_P4, (200, 200, 200, 200)),
            2,
            _POSTS4 + (
                _total_to("Utg", 30, 2),
                _fold("Btn", 3),
                _fold("Sb", 4),
                _fold("Bb", 5),
            ),
        ),
        # G-H9 [3max]: BTN fold, SB open (SB open IS a steal pin), BB fold.
        "G-H9": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _fold("Btn", 2),
                _total_to("Sb", 30, 3),
                _fold("Bb", 4),
            ),
        ),
        # G-H10 [4max]: UTG limp, BTN open, SB fold, BB call (limped caller
        # present and ALLOWED for BB defense per D2/catalog pin).
        "G-H10": (
            _make_table(_P4, (200, 200, 200, 200)),
            2,
            _POSTS4 + (
                _call_total("Utg", 10, 2),
                _total_to("Btn", 30, 3),
                _fold("Sb", 4),
                _call_total("Bb", 30, 5),
            ),
        ),
        # G-H11 [3max]: BTN open, SB 3bet to60, BB fold, BTN fold
        # (BB faces rc=2: not a single-open defense).
        "G-H11": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _total_to("Btn", 30, 2),
                _total_to("Sb", 60, 3),
                _fold("Bb", 4),
                _fold("Btn", 5),
            ),
        ),
        # G-H12 [3max]: same shape as G-H1 — the within-one-hand HU/multiway
        # flip proof (D4): preflop decisions with 3 active carry
        # heads_up=False, flop decisions with 2 active carry heads_up=True.
        "G-H12": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _total_to("Btn", 30, 2),
                _fold("Sb", 3),
                _call_total("Bb", 30, 4),
                _check("Bb", 5, Street.FLOP),
                _bet_added("Btn", 10, 6),
                _fold("Bb", 7, Street.FLOP),
            ),
        ),
        # G-H13 [3max]: BTN open, SB fold, BB call; flop: BB DONK bet 10,
        # BTN fold (bettor != preflop final aggressor => no c-bet stats).
        "G-H13": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (
                _total_to("Btn", 30, 2),
                _fold("Sb", 3),
                _call_total("Bb", 30, 4),
                _bet_added("Bb", 10, 5),
                _fold("Btn", 6, Street.FLOP),
            ),
        ),
        # G-FAI [HU, stacks 30/25]: BTN open-shove all-in to30, BB fold.
        # BB's facing-all-in snapshot legal set observed {FOLD, CALL} (U3):
        # rule D3(iii) KEEPS it as an opportunity (fold-vs-shove is genuine).
        "G-FAI": (
            _make_table(_HU, (30, 25)),
            2,
            _POSTS_HU + (
                _total_to("Btn", 30, 2),
                _fold("Bb", 3),
            ),
        ),
    }


FIXTURES = _fixtures()

_SIX_IDS = frozenset(
    {"three_bet", "fold_to_three_bet", "cbet", "fold_to_cbet", "steal", "bb_defense"}
)


def _snap(
    actor,
    street=Street.PREFLOP,
    action_index=1,
    legal=None,
    active=None,
    effective=None,
    to_call=None,
):
    """Synthetic DecisionSnapshot for pure-helper-level pins (X01/W03/U1)."""
    names = tuple(active) if active is not None else ("Btn", "Sb", "Bb")
    kinds = tuple(legal) if legal is not None else (
        ActionKind.FOLD,
        ActionKind.CALL,
        ActionKind.RAISE,
    )
    stacks = dict(effective) if effective is not None else {name: 100 for name in names}
    calls = dict(to_call) if to_call is not None else {name: 0 for name in names}
    return DecisionSnapshot(
        SCHEMA_VERSION,
        STATE_VERSION,
        street,
        action_index,
        actor,
        kinds,
        names,
        30,
        stacks,
        calls,
        20,
        False,
    )


class OpportunityTestBase(unittest.TestCase):
    """Lazy-import setUp (Phase A: ModuleNotFoundError per test; Phase B:
    NotImplementedError; GREEN: real modules) + golden-hand accessors."""

    def setUp(self):
        self.opportunity = importlib.import_module("poker.stats.opportunity")
        self.definitions = importlib.import_module("poker.stats.definitions")

    def _evaluate(self, name):
        table, button_seat, actions = FIXTURES[name]
        return self.opportunity.evaluate_hand(
            table, table.blinds, actions, button_seat=button_seat
        )

    def _counts(self, name):
        return self._evaluate(name).count_by_stat()

    def _matches(self, name, stat_id):
        return [
            match
            for match in self._evaluate(name).matches
            if match.stat_id == stat_id
        ]

    def _assert_totals(self, name, expected):
        """expected: {stat_id: (successes, opportunities)} — exact pairs."""
        counts = self._counts(name)
        self.assertEqual(set(counts), set(_SIX_IDS))
        for stat_id, (successes, opportunities) in expected.items():
            self.assertEqual(
                counts[stat_id],
                self.opportunity.StatCount(stat_id, successes, opportunities),
                "%s/%s" % (name, stat_id),
            )


class SkeletonAndStructureTests(OpportunityTestBase):
    """K01-K03: lazy-import RED design + package markers."""

    def test_K01_skeleton_imports_resolve(self):
        self.assertIsNotNone(self.opportunity)
        self.assertIsNotNone(self.definitions)
        for name in (
            "evaluate_hand",
            "OpportunityMatch",
            "StatCount",
            "HandEvaluation",
            "is_decision_opportunity",
            "is_heads_up",
            "decided_action",
        ):
            self.assertTrue(callable(getattr(self.opportunity, name)), name)
        for name in (
            "DEFINITIONS_VERSION",
            "StatDefinition",
            "list_definitions",
            "get_definition",
            "definitions_to_dict",
        ):
            self.assertTrue(hasattr(self.definitions, name), name)

    def test_K02_no_module_level_stats_imports(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as handle:
            source = handle.read()
        for lineno, line in enumerate(source.splitlines(), 1):
            if re.match(r"^(import|from)\s+poker\.stats\b", line):
                self.fail(
                    "module-level poker.stats import at line %d: %s" % (lineno, line)
                )

    def test_K03_package_markers_present(self):
        self.assertTrue(
            os.path.isfile(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
            ),
            "tests/stats/__init__.py must exist",
        )
        stats_dir = os.path.dirname(os.path.abspath(self.definitions.__file__))
        self.assertTrue(
            os.path.isfile(os.path.join(stats_dir, "__init__.py")),
            "src/poker/stats/__init__.py must exist",
        )


class DefinitionsContractTests(OpportunityTestBase):
    """D01-D05: versioned + introspectable definitions (AC3)."""

    def test_D01_definitions_version_is_1_0_0_str(self):
        version = self.definitions.DEFINITIONS_VERSION
        self.assertEqual(version, "1.0.0")
        self.assertIs(type(version), str)

    def test_D02_list_definitions_six_pinned_ids(self):
        definitions = self.definitions.list_definitions()
        self.assertEqual(len(definitions), 6)
        self.assertEqual({d.stat_id for d in definitions}, set(_SIX_IDS))
        by_id = {d.stat_id: d for d in definitions}
        for stat_id in ("steal", "three_bet", "fold_to_three_bet", "bb_defense"):
            self.assertEqual(by_id[stat_id].streets, frozenset({Street.PREFLOP}))
        for stat_id in ("cbet", "fold_to_cbet"):
            self.assertEqual(by_id[stat_id].streets, frozenset({Street.FLOP}))
        self.assertEqual(by_id["steal"].dimensions, ("position", "heads_up"))
        self.assertEqual(by_id["three_bet"].dimensions, ("position", "heads_up"))
        self.assertEqual(by_id["cbet"].dimensions, ("position", "heads_up"))
        self.assertEqual(
            by_id["fold_to_three_bet"].dimensions,
            ("position", "opener_position", "heads_up"),
        )
        self.assertEqual(by_id["bb_defense"].dimensions, ("opener_position", "heads_up"))
        # F10 extension (documented): fold_to_cbet carries opener_position
        # keyed to the preflop final aggressor's position.
        self.assertEqual(
            by_id["fold_to_cbet"].dimensions,
            ("position", "opener_position", "heads_up"),
        )

    def test_D03_get_definition_version_and_specs(self):
        definition = self.definitions.get_definition("three_bet")
        self.assertEqual(
            definition.definition_version, self.definitions.DEFINITIONS_VERSION
        )
        self.assertTrue(definition.predicate_spec)
        self.assertTrue(definition.success_spec)
        self.assertTrue(definition.exclusions)
        self.assertIn("D3", definition.exclusions)

    def test_D04_get_definition_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            self.definitions.get_definition("no_such_stat")

    def test_D05_definitions_to_dict_roundtrip_and_frozen(self):
        mapping = self.definitions.definitions_to_dict()
        self.assertEqual(set(mapping), set(_SIX_IDS))
        for stat_id, payload in mapping.items():
            self.assertEqual(payload["stat_id"], stat_id)
            self.assertEqual(
                payload["definition_version"], self.definitions.DEFINITIONS_VERSION
            )
            self.assertTrue(payload["predicate_spec"])
            self.assertTrue(payload["success_spec"])
            self.assertTrue(payload["exclusions"])
        definition = self.definitions.get_definition("steal")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            definition.stat_id = "mutated"


class StealGoldenTests(OpportunityTestBase):
    """S01-S05: steal attempt = unopened CO/BTN/SB open; success = RAISE."""

    def test_S01_steal_positive_golden_H1(self):
        self._assert_totals("G-H1", {"steal": (1, 1)})
        matches = self._matches("G-H1", "steal")
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertTrue(match.succeeded)
        self.assertEqual(match.action_index, 2)  # BTN's open RAISE (decided action)
        self.assertEqual(dict(match.dimensions), {"position": "BTN"})
        self.assertFalse(match.heads_up)

    def test_S02_steal_btn_fold_success_false_H2(self):
        self._assert_totals("G-H2", {"steal": (0, 1)})
        match = self._matches("G-H2", "steal")[0]
        self.assertFalse(match.succeeded)  # HU BTN open: attempt, FOLD no success
        self.assertTrue(match.heads_up)

    def test_S03_steal_sb_open_counts_H9(self):
        # ORACLE CORRECTION (module docstring): the designer asserted (1,1);
        # BTN's unopened FOLD decision is ALSO a steal attempt (rc == 0,
        # cc == 0, position BTN) by the designer's own G-H2 pin, so the
        # D2-derived pair is (1,2): SB's open succeeds, BTN's fold does not.
        self._assert_totals("G-H9", {"steal": (1, 2)})
        sb_match = [
            match for match in self._matches("G-H9", "steal")
            if match.dimensions["position"] == "SB"
        ][0]
        self.assertTrue(sb_match.succeeded)
        self.assertEqual(dict(sb_match.dimensions), {"position": "SB"})
        btn_match = [
            match for match in self._matches("G-H9", "steal")
            if match.dimensions["position"] == "BTN"
        ][0]
        self.assertFalse(btn_match.succeeded)

    def test_S04_steal_utg_open_not_steal_H7(self):
        self._assert_totals(
            "G-H7",
            {
                "steal": (0, 0),
                "three_bet": (0, 3),
                "fold_to_three_bet": (3, 3),
                "bb_defense": (0, 1),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )

    def test_S05_steal_unopened_limp_not_success_H3(self):
        self._assert_totals("G-H3", {"steal": (0, 1)})
        match = self._matches("G-H3", "steal")[0]
        self.assertFalse(match.succeeded)  # BTN CALL (limp) is NOT a steal success


class BbDefenseGoldenTests(OpportunityTestBase):
    """B01-B05: BB faces a single preflop open; success = CALL or RAISE."""

    def test_B01_bb_defense_positive_golden_H1(self):
        # heads_up correction (see module docstring): SB folded before BB's
        # decision, so per D4 the BB decision point has 2 active players.
        self._assert_totals("G-H1", {"bb_defense": (1, 1)})
        match = self._matches("G-H1", "bb_defense")[0]
        self.assertTrue(match.succeeded)  # defended via CALL
        self.assertEqual(match.action_index, 4)
        self.assertEqual(dict(match.dimensions), {"opener_position": "BTN"})
        self.assertTrue(match.heads_up)

    def test_B02_bb_defense_fold_not_defended_H9(self):
        self._assert_totals("G-H9", {"bb_defense": (0, 1)})
        self.assertFalse(self._matches("G-H9", "bb_defense")[0].succeeded)

    def test_B03_bb_defense_limped_callers_allowed_H10(self):
        self._assert_totals(
            "G-H10",
            {
                "bb_defense": (1, 1),
                "steal": (0, 0),
                "three_bet": (0, 0),
                "fold_to_three_bet": (0, 0),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )
        match = self._matches("G-H10", "bb_defense")[0]
        self.assertTrue(match.succeeded)
        self.assertEqual(dict(match.dimensions), {"opener_position": "BTN"})

    def test_B04_bb_defense_facing_3bet_excluded_H11(self):
        self._assert_totals(
            "G-H11",
            {
                "bb_defense": (0, 0),
                "steal": (1, 1),
                "three_bet": (1, 1),
                "fold_to_three_bet": (0, 1),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )

    def test_B05_bb_defense_limped_pot_no_opportunity_H4(self):
        self._assert_totals(
            "G-H4",
            {
                "bb_defense": (0, 0),
                "steal": (0, 1),
                "three_bet": (0, 0),
                "fold_to_three_bet": (0, 0),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )


class ThreeBetGoldenTests(OpportunityTestBase):
    """T01-T03: raise over a LONE open (rc==1, cc==0); success = RAISE."""

    def test_T01_three_bet_positive_golden_H1(self):
        # ORACLE CORRECTION (module docstring): designer asserted (1,2) with
        # "BB success=True"; per D2 success = decided RAISE, SB decided FOLD
        # and BB decided CALL => (0, 2). G-H13 (identical shape) agrees.
        self._assert_totals("G-H1", {"three_bet": (0, 2)})
        indices = sorted(match.action_index for match in self._matches("G-H1", "three_bet"))
        self.assertEqual(indices, [3, 4])  # BTN's own open (index 2) is NOT a match
        sb_match = self._matches("G-H1", "three_bet")[0]
        self.assertFalse(sb_match.succeeded)
        self.assertEqual(dict(sb_match.dimensions), {"position": "SB"})

    def test_T02_three_bet_iso_raise_over_limpers_excluded_H4(self):
        self._assert_totals("G-H4", {"three_bet": (0, 0)})

    def test_T03_three_bet_3bet_over_caller_excluded_H6(self):
        self._assert_totals("G-H6", {"three_bet": (0, 1)})
        match = self._matches("G-H6", "three_bet")[0]
        self.assertFalse(match.succeeded)  # SB decided CALL
        self.assertEqual(match.action_index, 3)


class FoldToThreeBetGoldenTests(OpportunityTestBase):
    """F01-F03: folds vs a lone open; opener_position dimension."""

    def test_F01_fold_to_three_bet_positive_golden_H1(self):
        # ORACLE CORRECTION (module docstring): the designer asserted (1,1);
        # BB's preflop decision (rc == 1, cc == 0, decided CALL) is an
        # identical fold_to_three_bet OPPORTUNITY per D2 (only `succeeded`
        # differs), and the designer's own G-H6/G-H11 count decided
        # CALL/RAISE as fold_to_three_bet opportunities ((0,1) each), so the
        # D2-derived pair is (1,2).
        self._assert_totals("G-H1", {"fold_to_three_bet": (1, 2)})
        sb_match = [
            match for match in self._matches("G-H1", "fold_to_three_bet")
            if match.dimensions["position"] == "SB"
        ][0]
        self.assertTrue(sb_match.succeeded)
        self.assertEqual(sb_match.action_index, 3)
        self.assertEqual(
            dict(sb_match.dimensions), {"position": "SB", "opener_position": "BTN"}
        )
        bb_match = [
            match for match in self._matches("G-H1", "fold_to_three_bet")
            if match.dimensions["position"] == "BB"
        ][0]
        self.assertFalse(bb_match.succeeded)  # BB decided CALL, not FOLD

    def test_F02_fold_to_three_bet_opener_reopen_excluded_H6(self):
        self._assert_totals(
            "G-H6",
            {
                "steal": (1, 1),
                "three_bet": (0, 1),
                "fold_to_three_bet": (0, 1),
                "bb_defense": (1, 1),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )
        evaluation = self._evaluate("G-H6")
        indices = sorted({match.action_index for match in evaluation.matches})
        self.assertEqual(indices, [2, 3, 4])  # re-opened decisions (5, 6) match nothing
        match = self._matches("G-H6", "fold_to_three_bet")[0]
        self.assertFalse(match.succeeded)  # SB decided CALL, not FOLD
        self.assertEqual(match.action_index, 3)

    def test_F03_fold_to_three_bet_over_caller_excluded_H4(self):
        self._assert_totals("G-H4", {"fold_to_three_bet": (0, 0)})


class CbetGoldenTests(OpportunityTestBase):
    """C01-C03: flop bet by the preflop final aggressor with no flop
    aggression before it; success = BET."""

    def test_C01_cbet_positive_golden_H1(self):
        self._assert_totals("G-H1", {"cbet": (1, 1)})
        match = self._matches("G-H1", "cbet")[0]
        self.assertTrue(match.succeeded)
        self.assertEqual(match.action_index, 6)
        self.assertEqual(dict(match.dimensions), {"position": "BTN"})
        # heads_up correction (module docstring): flop decision has 2 active.
        self.assertTrue(match.heads_up)

    def test_C02_cbet_limped_pot_no_aggressor_H3(self):
        self._assert_totals("G-H3", {"cbet": (0, 0)})

    def test_C03_cbet_donk_not_cbet_H13(self):
        # fold_to_three_bet is (1,2) here per the F01 oracle correction
        # (BB's preflop CALL decision is an opportunity, not a success).
        self._assert_totals(
            "G-H13",
            {
                "cbet": (0, 0),
                "steal": (1, 1),
                "three_bet": (0, 2),
                "fold_to_three_bet": (1, 2),
                "bb_defense": (1, 1),
                "fold_to_cbet": (0, 0),
            },
        )


class FoldToCbetGoldenTests(OpportunityTestBase):
    """F10-F11: folds vs a TRUE c-bet (flop bettor == preflop aggressor)."""

    def test_F10_fold_to_cbet_positive_golden_H1(self):
        self._assert_totals("G-H1", {"fold_to_cbet": (1, 1)})
        match = self._matches("G-H1", "fold_to_cbet")[0]
        self.assertTrue(match.succeeded)
        self.assertEqual(match.action_index, 7)
        self.assertEqual(
            dict(match.dimensions), {"position": "BB", "opener_position": "BTN"}
        )
        self.assertTrue(match.heads_up)

    def test_F11_fold_to_cbet_donk_excluded_H13(self):
        self._assert_totals("G-H13", {"fold_to_cbet": (0, 0)})


class ExclusionTests(OpportunityTestBase):
    """X01-X03: D3 all-in / no-decision exclusion rule."""

    def test_X01_exclusion_fold_only_and_already_all_in_helper(self):
        is_opportunity = self.opportunity.is_decision_opportunity
        self.assertTrue(is_opportunity(_snap("Btn")))
        # Rule (iii): fold-only legal set carries no strategic choice.
        self.assertFalse(is_opportunity(_snap("Btn", legal=(ActionKind.FOLD,))))
        self.assertFalse(is_opportunity(_snap("Btn", legal=())))
        # Rule (ii): an already all-in actor has no remaining decision.
        self.assertFalse(
            is_opportunity(_snap("Btn", effective={"Btn": 0, "Sb": 100, "Bb": 100}))
        )
        # Rule (i): actor must be an active player at the decision point.
        self.assertFalse(is_opportunity(_snap("Btn", active=("Sb", "Bb"))))
        # Terminal snapshots (actor None) never produce matches.
        self.assertFalse(is_opportunity(_snap(None)))

    def test_X02_exclusion_facing_allin_fold_call_kept_FAI(self):
        # U3: replay-verified — BB's facing-all-in legal set is {FOLD, CALL}.
        table, button_seat, actions = FIXTURES["G-FAI"]
        snapshots, _result = StateEngine.replay(
            table, table.blinds, actions, button_seat=button_seat
        )
        bb_snapshot = [s for s in snapshots if s.actor_to_act == "Bb"][0]
        self.assertIn(ActionKind.FOLD, bb_snapshot.legal_actions)
        self.assertIn(ActionKind.CALL, bb_snapshot.legal_actions)
        self.assertNotIn(ActionKind.RAISE, bb_snapshot.legal_actions)
        self._assert_totals(
            "G-FAI",
            {
                "steal": (1, 1),
                "three_bet": (0, 1),
                "fold_to_three_bet": (1, 1),
                "bb_defense": (0, 1),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )
        evaluation = self._evaluate("G-FAI")
        self.assertEqual(len(evaluation.matches), 4)
        for match in evaluation.matches:
            self.assertTrue(match.heads_up)  # HU table: 2 active everywhere

    def test_X03_exclusion_terminal_snapshot_no_match_H2(self):
        evaluation = self._evaluate("G-H2")
        self.assertEqual(len(evaluation.matches), 1)  # only BTN's steal attempt
        self.assertEqual(evaluation.matches[0].stat_id, "steal")


class HuMultiwayTests(OpportunityTestBase):
    """W01-W03: D4 heads-up/multiway + D5 re-opened granularity."""

    def test_W01_hu_multiway_flip_H12_vs_H2(self):
        evaluation = self._evaluate("G-H12")
        steal_match = self._matches("G-H12", "steal")[0]
        self.assertFalse(steal_match.heads_up)  # 3 active at BTN's open
        cbet_match = self._matches("G-H12", "cbet")[0]
        fold_cbet_match = self._matches("G-H12", "fold_to_cbet")[0]
        self.assertTrue(cbet_match.heads_up)  # 2 active at the flop decision
        self.assertTrue(fold_cbet_match.heads_up)
        # D4-at-decision-point note: BB's preflop decision happens after SB
        # folded (2 active), so heads_up=True there too (module docstring).
        bb_match = self._matches("G-H12", "bb_defense")[0]
        self.assertTrue(bb_match.heads_up)
        hu_match = self._evaluate("G-H2").matches[0]
        self.assertTrue(hu_match.heads_up)

    def test_W02_reopened_double_opportunity_no_dedupe_H6(self):
        evaluation = self._evaluate("G-H6")
        three_bet = self._matches("G-H6", "three_bet")
        fold_three = self._matches("G-H6", "fold_to_three_bet")
        self.assertEqual(len(three_bet), 1)
        self.assertEqual(len(fold_three), 1)
        # ONE decision point carries BOTH a three_bet and a fold_to_three_bet
        # match: no per-(street, seat) first-wins dedupe is imported.
        self.assertEqual(three_bet[0].action_index, 3)
        self.assertEqual(fold_three[0].action_index, 3)
        indices = sorted({match.action_index for match in evaluation.matches})
        self.assertEqual(indices, [2, 3, 4])
        self._assert_totals(
            "G-H6",
            {
                "steal": (1, 1),
                "three_bet": (0, 1),
                "fold_to_three_bet": (0, 1),
                "bb_defense": (1, 1),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )

    def test_W03_fewer_than_two_active_fails_closed(self):
        lonely = _snap("Btn", active=("Btn",))
        with self.assertRaises(ValueError):
            self.opportunity.is_heads_up(lonely)


class CountApiTests(OpportunityTestBase):
    """Z01-Z03: zero-opportunity rate, determinism, purity."""

    def test_Z01_zero_opportunity_rate_none_H3(self):
        counts = self._counts("G-H3")
        self.assertEqual(counts["cbet"], self.opportunity.StatCount("cbet", 0, 0))
        self.assertIsNone(counts["cbet"].rate())  # never 0.0 for 0 opportunities
        self._assert_totals(
            "G-H3",
            {
                "steal": (0, 1),
                "three_bet": (0, 0),
                "fold_to_three_bet": (0, 0),
                "bb_defense": (0, 0),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )

    def test_Z02_determinism_byte_identical_rerun(self):
        def render(evaluation):
            return json.dumps(
                [
                    [
                        match.stat_id,
                        match.actor,
                        match.street.name,
                        match.action_index,
                        match.succeeded,
                        match.heads_up,
                        dict(sorted(match.dimensions.items())),
                    ]
                    for match in evaluation.matches
                ],
                sort_keys=True,
            )

        first = self._evaluate("G-H1")
        second = self._evaluate("G-H1")
        self.assertEqual(render(first), render(second))
        self.assertEqual(first.matches, second.matches)
        self.assertEqual(first.count_by_stat(), second.count_by_stat())

    def test_Z03_purity_no_sqlalchemy_no_io(self):
        for module in (self.opportunity, self.definitions):
            origin = importlib.util.find_spec(module.__name__).origin
            self.assertIsNotNone(origin)
            with open(origin, encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("sqlalchemy", source)
            self.assertNotIn("open(", source)
            self.assertNotIn("Path(", source)


class BoundaryTests(OpportunityTestBase):
    """B01X-B03X: fail-closed boundaries + frozen result dataclasses."""

    def test_B01X_button_seat_unknown_raises(self):
        table, _button, actions = FIXTURES["G-H1"]
        with self.assertRaises(ValueError):
            self.opportunity.evaluate_hand(
                table, table.blinds, actions, button_seat=99
            )

    def test_B02X_non_action_element_fails_closed(self):
        table, button_seat, actions = FIXTURES["G-H1"]
        with self.assertRaises(TypeError):
            self.opportunity.evaluate_hand(
                table,
                table.blinds,
                list(actions) + ["not-an-action"],
                button_seat=button_seat,
            )

    def test_B03X_frozen_result_dataclasses(self):
        match = self._evaluate("G-H2").matches[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            match.succeeded = True
        count = self.opportunity.StatCount("steal", 0, 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            count.successes = 1
        evaluation = self._evaluate("G-H2")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            evaluation.matches = ()


class CorrespondenceHelperTests(OpportunityTestBase):
    """U1 resolution: decided_action is the pure correspondence helper
    (snapshot.action_index + 1 must index a real, non-POST action by the
    snapshot actor, else ValueError); tested directly with synthetic pairs."""

    def _action(self, actor, kind, seq, amount=None, semantics=None):
        if kind is ActionKind.POST:
            amount = Chips(amount if amount is not None else 5)
            semantics = AmountSemantics.ADDED
        elif kind in (ActionKind.CALL, ActionKind.RAISE):
            amount = Chips(amount if amount is not None else 10)
            semantics = semantics or AmountSemantics.TOTAL_TO
        return Action(actor, Street.PREFLOP, kind, amount, semantics, seq, _PROV)

    def test_decided_action_valid_pair(self):
        actions = [
            self._action("Sb", ActionKind.POST, 0),
            self._action("Btn", ActionKind.RAISE, 1),
        ]
        snapshot = _snap("Btn", action_index=0)
        index, action = self.opportunity.decided_action(snapshot, actions)
        self.assertEqual(index, 1)
        self.assertIs(action, actions[1])

    def test_decided_action_index_out_of_range_raises(self):
        actions = [self._action("Btn", ActionKind.FOLD, 0)]
        snapshot = _snap("Sb", action_index=1)
        with self.assertRaises(ValueError):
            self.opportunity.decided_action(snapshot, actions)

    def test_decided_action_actor_mismatch_raises(self):
        actions = [self._action("Btn", ActionKind.FOLD, 0)]
        snapshot = _snap("Sb", action_index=-1)
        with self.assertRaises(ValueError):
            self.opportunity.decided_action(snapshot, actions)

    def test_decided_action_post_successor_raises(self):
        actions = [self._action("Btn", ActionKind.POST, 0)]
        snapshot = _snap("Sb", action_index=-1)
        with self.assertRaises(ValueError):
            self.opportunity.decided_action(snapshot, actions)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
