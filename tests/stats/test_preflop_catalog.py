"""Behavior-defining tests for the preflop statistic catalog (R2, TDD).

Task POKER-STATS-PREFLOP-001 (risk R2, important_calculation). Two-phase
lazy-import RED design (mirrors tests/stats/test_opportunities.py): there are
NO module-level imports of ``poker.stats.catalog``; ``setUp`` reaches the
catalog layer through ``importlib.import_module`` calls. Phase A therefore
fails per test (zero collection errors); the RED stubs fail behaviorally at
``NotImplementedError``; GREEN provides the real modules.

Oracle (hand-derived from the adopted plan's pinned predicate semantics; every
expected (successes, opportunities) pair is hand-derived and asserted
exactly; fixtures are replay-legality-verified against ``StateEngine.replay``
BEFORE totals are asserted — fixture bugs fix the fixture, never the rule):

* Combined registry: the 6 pinned definitions from ``poker.stats.definitions``
  (fixed order: three_bet, fold_to_three_bet, cbet, fold_to_cbet, steal,
  bb_defense) + 17 new catalog definitions in the plan's fixed order; no id
  collisions; every definition machine-readable (predicate_spec/success_spec/
  dimensions/exclusions) via ``preflop_definitions_to_dict`` (AC1); every
  definition exposes ``definition_version`` (AC3); the predicate vocabulary
  is CLOSED and asserted exactly.
* Golden fixtures (position-vs-position and facing-action variants):
  P-H1 (BTN open/cold-call, flop continuation), P-H2 (SB complete + BB check
  NEGATIVE: a check is never a vpip success), P-H3 (open-limp/over-limp),
  P-H4 (BB iso-raise; fold_to_iso keyed to the iso-raiser position BB),
  P-H6/P-H7/P-H8 (4-bet/5-bet ladder incl. 5-bet success and a fold facing a
  5-bet that matches nothing), P-H9 (squeeze family, BB squizzer vs UTG/BTN
  responses), P-H10 (CO open: BB/SB/BTN responses vs a CO opener),
  P-H11 (BTN iso-raise over a limper; SB fold / BB call vs the BTN opener),
  P-H12 (limp-then-raise pin: the raise after a limp is NEVER a pfr success),
  P-H13 (re-opened decisions are never vpip/pfr opportunities),
  P-H15 (multiway iso over three limpers).
* The six pinned stats' (successes, opportunities) pairs stay identical to
  tests/stats/test_opportunities.py expectations (continuity test compares
  the composed counts against the frozen ``evaluate_hand`` for every fixture,
  plus hand-asserted totals for P-H1/P-H10).
* One decision point may carry several stat matches (D5), but never two
  matches of the SAME stat_id (asserted for every fixture).
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
from poker.engine.state import StateEngine
from tests.domain.test_pots import _check, _fold, _post, _total_to

_PROV = "test:stats:preflop-catalog"


# ---------------------------------------------------------------------------
# Action builders (canonical game.Action instances; _post/_total_to/_fold/
# _check shared from tests.domain.test_pots per the test_state_machine pin)
# ---------------------------------------------------------------------------


def _call_total(actor, total, seq, street=Street.PREFLOP):
    return Action(
        actor,
        street,
        ActionKind.CALL,
        Chips(total),
        AmountSemantics.TOTAL_TO,
        seq,
        _PROV,
    )


def _bet_added(actor, amount, seq, street=Street.FLOP):
    return Action(
        actor, street, ActionKind.BET, Chips(amount), AmountSemantics.ADDED, seq, _PROV
    )


# ---------------------------------------------------------------------------
# Golden fixtures (all engine-legal; replay-verified before totals asserted)
# ---------------------------------------------------------------------------


def _make_table(names, stacks, sb=5, bb=10):
    """3-max: seats 2=BTN, 3=SB, 4=BB; 4-max adds seat 5=UTG; 5-max adds
    seat 6=CO; HU would be seats 2/3."""
    players = tuple(Player(name, Chips(stack)) for name, stack in zip(names, stacks))
    seats = tuple(Seat(2 + index) for index in range(len(names)))
    blinds = BlindAnteConfig(Chips(sb), Chips(bb), Chips(0))
    return TableMetadata(players, seats, blinds)


_P3 = ("Btn", "Sb", "Bb")
_P4 = ("Btn", "Sb", "Bb", "Utg")
_P5 = ("Btn", "Sb", "Bb", "Utg", "Co")
_POSTS3 = (_post("Sb", 5, 0), _post("Bb", 10, 1))
_POSTS4 = (_post("Sb", 5, 0), _post("Bb", 10, 1))
_POSTS5 = (_post("Sb", 5, 0), _post("Bb", 10, 1))


def _fixtures():
    return {
        # P-H1 [= tests/stats G-H1; 3max, stacks 200]: BTN open, SB fold,
        # BB cold-call; flop: BB check, BTN c-bet 10, BB fold. Unopened /
        # facing-raise variants + the BTN-opener split for cold_call and
        # fold_to_three_bet; flop keeps the pinned cbet continuity.
        "P-H1": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3
            + (
                _total_to("Btn", 30, 2),
                _fold("Sb", 3),
                _call_total("Bb", 30, 4),
                _check("Bb", 5, Street.FLOP),
                _bet_added("Btn", 10, 6),
                _fold("Bb", 7, Street.FLOP),
            ),
        ),
        # P-H2 [3max]: BTN fold, SB complete (call to10), BB check.
        # Unopened facing-action variants: rfi/open_limp/sb_complete/steal
        # attempts; NEGATIVE pin: the BB check is NOT a vpip success.
        "P-H2": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3 + (_fold("Btn", 2), _call_total("Sb", 10, 3), _check("Bb", 4)),
        ),
        # P-H3 [3max]: BTN open-limp, SB over-limp, BB check.
        "P-H3": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3
            + (_call_total("Btn", 10, 2), _call_total("Sb", 10, 3), _check("Bb", 4)),
        ),
        # P-H4 [= tests/stats G-H4; 3max]: BTN limp, SB limp, BB iso-raise
        # to30, BTN fold, SB fold (fold_to_iso keyed to the BB iso-raiser).
        "P-H4": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3
            + (
                _call_total("Btn", 10, 2),
                _call_total("Sb", 10, 3),
                _total_to("Bb", 30, 4),
                _fold("Btn", 5),
                _fold("Sb", 6),
            ),
        ),
        # P-H6 [3max]: BTN open, SB 3bet, BB fold, BTN 4bet to120, SB fold
        # (BB acts before BTN's re-opened 4-bet: clockwise turn order).
        "P-H6": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3
            + (
                _total_to("Btn", 30, 2),
                _total_to("Sb", 60, 3),
                _fold("Bb", 4),
                _total_to("Btn", 120, 5),
                _fold("Sb", 6),
            ),
        ),
        # P-H7 [3max, stacks 400]: BTN open, SB 3bet to90, BB fold, BTN 4bet
        # to210, SB 5bet all-in to400, BTN fold (rc == 4 matches nothing: the
        # 'facing-4bet+' facing label is an aggregation cap, not a predicate).
        "P-H7": (
            _make_table(_P3, (400, 400, 400)),
            2,
            _POSTS3
            + (
                _total_to("Btn", 30, 2),
                _total_to("Sb", 90, 3),
                _fold("Bb", 4),
                _total_to("Btn", 210, 5),
                _total_to("Sb", 400, 6),
                _fold("Btn", 7),
            ),
        ),
        # P-H8 [3max]: BTN open, SB 3bet, BB fold, BTN 4bet, SB call
        # (SB's call faces the same 4-bet; call_to_four_bet success).
        "P-H8": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3
            + (
                _total_to("Btn", 30, 2),
                _total_to("Sb", 60, 3),
                _fold("Bb", 4),
                _total_to("Btn", 120, 5),
                _call_total("Sb", 120, 6),
            ),
        ),
        # P-H9 [4max]: UTG limp, BTN iso-raise, SB fold, BB squeeze to90,
        # UTG call, BTN fold (squeeze family; squizzer = BB).
        "P-H9": (
            _make_table(_P4, (200, 200, 200, 200)),
            2,
            _POSTS4
            + (
                _call_total("Utg", 10, 2),
                _total_to("Btn", 30, 3),
                _fold("Sb", 4),
                _total_to("Bb", 90, 5),
                _call_total("Utg", 90, 6),
                _fold("Btn", 7),
            ),
        ),
        # P-H10 [5max, seats 2..6]: UTG fold, CO open, BTN/SB/BB fold
        # (CO-opener split for cold_call / fold_to_three_bet / bb_defense).
        "P-H10": (
            _make_table(_P5, (200, 200, 200, 200, 200)),
            2,
            _POSTS5
            + (
                _fold("Utg", 2),
                _total_to("Co", 30, 3),
                _fold("Btn", 4),
                _fold("Sb", 5),
                _fold("Bb", 6),
            ),
        ),
        # P-H11 [4max]: UTG limp, BTN iso-raise, SB fold, BB call
        # (fold_to_iso/call_to_iso keyed to the BTN iso-raiser).
        "P-H11": (
            _make_table(_P4, (200, 200, 200, 200)),
            2,
            _POSTS4
            + (
                _call_total("Utg", 10, 2),
                _total_to("Btn", 30, 3),
                _fold("Sb", 4),
                _call_total("Bb", 30, 5),
            ),
        ),
        # P-H12 [3max]: BTN limp, SB limp, BB iso-raise, BTN raise to90,
        # SB fold, BB fold (squeeze success; limp-then-raise NEVER a pfr
        # success: BTN's raise is his SECOND decision).
        "P-H12": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3
            + (
                _call_total("Btn", 10, 2),
                _call_total("Sb", 10, 3),
                _total_to("Bb", 30, 4),
                _total_to("Btn", 90, 5),
                _fold("Sb", 6),
                _fold("Bb", 7),
            ),
        ),
        # P-H13 [= tests/stats G-H6; 3max]: BTN open, SB call, BB raise to90,
        # BTN fold (re-opened), SB fold (re-opened): re-opened second
        # decisions are never vpip/pfr opportunities.
        "P-H13": (
            _make_table(_P3, (200, 200, 200)),
            2,
            _POSTS3
            + (
                _total_to("Btn", 30, 2),
                _call_total("Sb", 30, 3),
                _total_to("Bb", 90, 4),
                _fold("Btn", 5),
                _fold("Sb", 6),
            ),
        ),
        # P-H15 [4max]: UTG/Btn/Sb limp, BB iso-raise to30, UTG call,
        # Btn fold, Sb fold (multiway iso; call_to_iso success).
        "P-H15": (
            _make_table(_P4, (200, 200, 200, 200)),
            2,
            _POSTS4
            + (
                _call_total("Utg", 10, 2),
                _call_total("Btn", 10, 3),
                _call_total("Sb", 10, 4),
                _total_to("Bb", 30, 5),
                _call_total("Utg", 30, 6),
                _fold("Btn", 7),
                _fold("Sb", 8),
            ),
        ),
    }


FIXTURES = _fixtures()

_PINNED_IDS = (
    "three_bet",
    "fold_to_three_bet",
    "cbet",
    "fold_to_cbet",
    "steal",
    "bb_defense",
)
_CATALOG_IDS = (
    "vpip",
    "pfr",
    "rfi",
    "open_limp",
    "sb_complete",
    "over_limp",
    "iso_raise",
    "fold_to_iso",
    "call_to_iso",
    "cold_call",
    "four_bet",
    "five_bet",
    "fold_to_four_bet",
    "call_to_four_bet",
    "squeeze",
    "fold_to_squeeze",
    "call_to_squeeze",
)
_ALL_IDS = _PINNED_IDS + _CATALOG_IDS

_PREDICATE_VOCABULARY = frozenset(
    {
        "street",
        "same_street_raise_count_before",
        "same_street_call_count_before",
        "same_street_call_count_min",
        "position_in",
        "actor_position",
        "first_preflop_decision",
        "actor_is",
        "street_bettor_is",
        "actor_is_not",
    }
)

_SUCCESS_ACTION_VOCABULARY = frozenset(
    {"FOLD", "CALL", "RAISE", "BET", "CHECK"}
)


class PreflopCatalogTestBase(unittest.TestCase):
    """Lazy-import setUp (Phase A: ModuleNotFoundError per test; the RED
    stubs fail at NotImplementedError; GREEN: real modules) + fixture
    accessors that replay-verify BEFORE totals are asserted."""

    def setUp(self):
        self.catalog = importlib.import_module("poker.stats.catalog.preflop")
        self.opportunity = importlib.import_module("poker.stats.opportunity")
        self.definitions = importlib.import_module("poker.stats.definitions")

    def _replay_verified(self, name):
        """Replay the fixture FIRST (fixture bugs fix the fixture)."""
        table, button_seat, actions = FIXTURES[name]
        snapshots, _result = StateEngine.replay(
            table, table.blinds, actions, button_seat=button_seat
        )
        self.assertTrue(snapshots, "%s produced no snapshots" % (name,))
        return table, button_seat, actions

    def _evaluate(self, name):
        table, button_seat, actions = self._replay_verified(name)
        return self.catalog.evaluate_preflop_hand(
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
        for stat_id, (successes, opportunities) in expected.items():
            self.assertEqual(
                counts[stat_id],
                self.opportunity.StatCount(stat_id, successes, opportunities),
                "%s/%s" % (name, stat_id),
            )

    def _assert_seventeen(self, name, expected):
        """Full 17-stat exact assertion (AC2 golden totals)."""
        self.assertEqual(
            frozenset(expected), frozenset(_CATALOG_IDS), name
        )
        self._assert_totals(name, expected)


class SkeletonAndRegistryTests(PreflopCatalogTestBase):
    """K01-K03: lazy-import RED design + the 23-id combined registry (AC1)."""

    def test_K01_skeleton_imports_resolve(self):
        self.assertIsNotNone(self.catalog)
        # SURFACE PIN (task.files): catalog ships as a NAMESPACE package —
        # poker.stats.catalog has NO __init__.py; only preflop.py exists.
        # Documented deviation from the plan's step-1 stub layout.
        self.assertIsNone(
            importlib.util.find_spec("poker.stats.catalog").origin,
            "poker.stats.catalog must be a namespace package (no __init__.py)",
        )
        for name in (
            "PREFLOP_DEFINITIONS_VERSION",
            "PREFLOP_PREDICATE_VOCABULARY",
            "StatRate",
            "PreflopEvaluation",
            "list_preflop_definitions",
            "get_preflop_definition",
            "preflop_definitions_to_dict",
            "evaluate_preflop_hand",
            "preflop_counts",
            "stat_rates",
        ):
            self.assertTrue(hasattr(self.catalog, name), name)

    def test_K02_no_module_level_catalog_imports(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as handle:
            source = handle.read()
        for lineno, line in enumerate(source.splitlines(), 1):
            if re.match(r"^(import|from)\s+poker\.stats\b", line):
                self.fail(
                    "module-level poker.stats import at line %d: %s" % (lineno, line)
                )

    def test_K03_namespace_package_loads_preflop(self):
        # SURFACE PIN: only preflop.py is published; the parent namespace
        # package must load it through the normal import system.
        module = importlib.import_module("poker.stats.catalog.preflop")
        self.assertIs(self.catalog, module)
        self.assertEqual(module.__name__, "poker.stats.catalog.preflop")

    def test_R01_registry_is_23_ids_fixed_order_no_collisions(self):
        definitions = self.catalog.list_preflop_definitions()
        ids = tuple(definition.stat_id for definition in definitions)
        self.assertEqual(len(ids), 23)
        self.assertEqual(len(set(ids)), 23, "duplicate stat ids")
        self.assertEqual(ids, _ALL_IDS)
        # The first six entries ARE the frozen definitions.py registry objects
        # (same order, same identity — no re-definition of pinned stats).
        pinned = self.definitions.list_definitions()
        for definition, frozen in zip(definitions, pinned):
            self.assertIs(definition, frozen)

    def test_R02_every_definition_exposes_definition_version(self):
        for definition in self.catalog.list_preflop_definitions():
            self.assertIs(type(definition.definition_version), str)
            self.assertTrue(definition.definition_version)
        new_definitions = self.catalog.list_preflop_definitions()[6:]
        for definition in new_definitions:
            self.assertEqual(
                definition.definition_version,
                self.catalog.PREFLOP_DEFINITIONS_VERSION,
                definition.stat_id,
            )
        self.assertEqual(self.catalog.PREFLOP_DEFINITIONS_VERSION, "1.0.0")
        self.assertEqual(self.definitions.DEFINITIONS_VERSION, "1.0.0")

    def test_R03_definitions_to_dict_machine_readable(self):
        mapping = self.catalog.preflop_definitions_to_dict()
        self.assertEqual(set(mapping), set(_ALL_IDS))
        for stat_id, payload in mapping.items():
            self.assertEqual(payload["stat_id"], stat_id)
            self.assertIs(type(payload["definition_version"]), str)
            self.assertTrue(payload["description"])
            self.assertTrue(payload["predicate_spec"], stat_id)
            self.assertTrue(payload["success_spec"], stat_id)
            self.assertTrue(payload["dimensions"], stat_id)
            self.assertIn("D3", payload["exclusions"], stat_id)
        # The 17 new definitions are preflop-only; the pinned cbet pair is
        # flop-scoped (frozen definitions.py, reused identically).
        for stat_id in _CATALOG_IDS:
            self.assertEqual(mapping[stat_id]["streets"], ["PREFLOP"], stat_id)
        for stat_id in ("cbet", "fold_to_cbet"):
            self.assertEqual(mapping[stat_id]["streets"], ["FLOP"], stat_id)
        for stat_id in ("three_bet", "fold_to_three_bet", "steal", "bb_defense"):
            self.assertEqual(mapping[stat_id]["streets"], ["PREFLOP"], stat_id)

    def test_R04_predicate_vocabulary_is_closed(self):
        used = set()
        for payload in self.catalog.preflop_definitions_to_dict().values():
            used.update(payload["predicate_spec"])
        self.assertEqual(used, _PREDICATE_VOCABULARY)
        self.assertEqual(
            self.catalog.PREFLOP_PREDICATE_VOCABULARY, _PREDICATE_VOCABULARY
        )

    def test_R05_success_actions_within_closed_vocabulary(self):
        for payload in self.catalog.preflop_definitions_to_dict().values():
            decided = payload["success_spec"]["decided_action"]
            actions = decided if isinstance(decided, list) else [decided]
            self.assertTrue(
                set(actions) <= _SUCCESS_ACTION_VOCABULARY,
                "%s: %s" % (payload["stat_id"], actions),
            )

    def test_R06_get_definition_unknown_id_fails_closed(self):
        with self.assertRaises(ValueError):
            self.catalog.get_preflop_definition("no_such_stat")

    def test_R07_definitions_are_frozen(self):
        definition = self.catalog.get_preflop_definition("vpip")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            definition.stat_id = "mutated"

    def test_R08_new_definition_dimensions_pins(self):
        by_id = {
            definition.stat_id: definition
            for definition in self.catalog.list_preflop_definitions()
        }
        for stat_id in (
            "vpip",
            "pfr",
            "rfi",
            "open_limp",
            "sb_complete",
            "over_limp",
            "iso_raise",
            "four_bet",
            "five_bet",
            "squeeze",
        ):
            self.assertEqual(by_id[stat_id].dimensions, ("position", "heads_up"))
        for stat_id in (
            "fold_to_iso",
            "call_to_iso",
            "cold_call",
            "fold_to_four_bet",
            "call_to_four_bet",
            "fold_to_squeeze",
            "call_to_squeeze",
        ):
            self.assertEqual(
                by_id[stat_id].dimensions,
                ("position", "opener_position", "heads_up"),
            )


class StatRateContractTests(PreflopCatalogTestBase):
    """ZR01-ZR03: frozen StatRate view + zero-filled counts (AC3)."""

    def test_ZR01_stat_rate_frozen_and_fields(self):
        rate = self.catalog.StatRate("vpip", "1.0.0", 2, 3, 2 / 3)
        self.assertEqual(rate.stat_id, "vpip")
        self.assertEqual(rate.definition_version, "1.0.0")
        self.assertEqual(rate.successes, 2)
        self.assertEqual(rate.opportunities, 3)
        self.assertEqual(rate.raw_rate, 2 / 3)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            rate.raw_rate = 1.0

    def test_ZR02_stat_rates_zero_fill_and_version(self):
        rates = self.catalog.stat_rates(self.catalog.preflop_counts([]))
        self.assertEqual(set(rates), set(_ALL_IDS))
        for stat_id, rate in rates.items():
            self.assertEqual(rate.stat_id, stat_id)
            self.assertEqual(
                rate.definition_version,
                self.catalog.get_preflop_definition(stat_id).definition_version,
            )
            self.assertEqual(rate.successes, 0)
            self.assertEqual(rate.opportunities, 0)
            self.assertIsNone(rate.raw_rate)  # never 0.0 on 0 opportunities

    def test_ZR03_counts_accumulate_and_rates_join(self):
        evaluation = self._evaluate("P-H1")
        counts = evaluation.count_by_stat()
        self.assertEqual(set(counts), set(_ALL_IDS))
        self.assertEqual(counts["cold_call"], self.opportunity.StatCount("cold_call", 1, 2))
        self.assertEqual(counts["sb_complete"], self.opportunity.StatCount("sb_complete", 0, 0))
        rates = evaluation.rates()
        self.assertEqual(
            rates["cold_call"],
            self.catalog.StatRate(
                "cold_call",
                self.catalog.get_preflop_definition("cold_call").definition_version,
                1,
                2,
                0.5,
            ),
        )
        self.assertIsNone(rates["sb_complete"].raw_rate)


class GoldenUnopenedTests(PreflopCatalogTestBase):
    """Unopened-pot facing-action variants: rfi / open_limp / sb_complete /
    vpip / pfr (+ the BB-check negative pin)."""

    def test_G01_rfi_open_P_H1(self):
        self._assert_seventeen(
            "P-H1",
            {
                "vpip": (2, 3),
                "pfr": (1, 3),
                "rfi": (1, 1),
                "open_limp": (0, 1),
                "sb_complete": (0, 0),
                "over_limp": (0, 0),
                "iso_raise": (0, 0),
                "fold_to_iso": (0, 0),
                "call_to_iso": (0, 0),
                "cold_call": (1, 2),
                "four_bet": (0, 0),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (0, 0),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        rfi = self._matches("P-H1", "rfi")[0]
        self.assertTrue(rfi.succeeded)
        self.assertEqual(rfi.action_index, 2)  # BTN's open
        self.assertEqual(dict(rfi.dimensions), {"position": "BTN"})
        self.assertFalse(rfi.heads_up)  # 3 active at the open
        cold_call = [
            match for match in self._matches("P-H1", "cold_call") if match.succeeded
        ][0]
        self.assertEqual(cold_call.action_index, 4)
        self.assertEqual(
            dict(cold_call.dimensions), {"position": "BB", "opener_position": "BTN"}
        )
        self.assertTrue(cold_call.heads_up)  # SB folded before BB's decision

    def test_G02_sb_complete_and_bb_check_negative_P_H2(self):
        self._assert_seventeen(
            "P-H2",
            {
                "vpip": (1, 3),
                "pfr": (0, 3),
                "rfi": (0, 2),
                "open_limp": (1, 2),
                "sb_complete": (1, 1),
                "over_limp": (0, 1),
                "iso_raise": (0, 1),
                "fold_to_iso": (0, 0),
                "call_to_iso": (0, 0),
                "cold_call": (0, 0),
                "four_bet": (0, 0),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (0, 0),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        complete = self._matches("P-H2", "sb_complete")[0]
        self.assertTrue(complete.succeeded)
        self.assertEqual(dict(complete.dimensions), {"position": "SB"})
        # D5 overlap: open_limp matches at BOTH the BTN fold (index 2, the
        # unopened CALL opportunity that failed) and SB's complete (index 3,
        # which is also an sb_complete match at the same decision point).
        open_limp = self._matches("P-H2", "open_limp")
        self.assertEqual(len(open_limp), 2)
        self.assertEqual(
            sorted(match.action_index for match in open_limp), [2, 3]
        )
        self.assertFalse(open_limp[0].succeeded)  # BTN's fold (index 2)
        self.assertTrue(open_limp[1].succeeded)  # SB's complete (index 3)
        # NEGATIVE pin: BB's check is a vpip OPPORTUNITY but never a success.
        bb_vpip = [
            match
            for match in self._matches("P-H2", "vpip")
            if match.dimensions["position"] == "BB"
        ][0]
        self.assertFalse(bb_vpip.succeeded)

    def test_G03_limp_family_P_H3(self):
        self._assert_seventeen(
            "P-H3",
            {
                "vpip": (2, 3),
                "pfr": (0, 3),
                "rfi": (0, 1),
                "open_limp": (1, 1),
                "sb_complete": (0, 0),
                "over_limp": (1, 2),
                "iso_raise": (0, 2),
                "fold_to_iso": (0, 0),
                "call_to_iso": (0, 0),
                "cold_call": (0, 0),
                "four_bet": (0, 0),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (0, 0),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )


class GoldenIsoTests(PreflopCatalogTestBase):
    """Limped facing-raise variants: iso_raise / fold_to_iso / call_to_iso,
    keyed to the iso-raiser position (BB in P-H4/P-H15, BTN in P-H11)."""

    def test_G04_iso_bb_opener_P_H4(self):
        self._assert_seventeen(
            "P-H4",
            {
                "vpip": (3, 3),
                "pfr": (1, 3),
                "rfi": (0, 1),
                "open_limp": (1, 1),
                "sb_complete": (0, 0),
                "over_limp": (1, 2),
                "iso_raise": (1, 2),
                "fold_to_iso": (2, 2),
                "call_to_iso": (0, 2),
                "cold_call": (0, 0),
                "four_bet": (0, 0),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (0, 2),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        iso = [
            match for match in self._matches("P-H4", "iso_raise") if match.succeeded
        ][0]
        self.assertTrue(iso.succeeded)
        self.assertEqual(dict(iso.dimensions), {"position": "BB"})
        for match in self._matches("P-H4", "fold_to_iso"):
            self.assertTrue(match.succeeded)
            self.assertEqual(match.dimensions["opener_position"], "BB")
        self.assertEqual(
            sorted(
                match.dimensions["position"]
                for match in self._matches("P-H4", "fold_to_iso")
            ),
            ["BTN", "SB"],
        )

    def test_G05_iso_btn_opener_P_H11(self):
        self._assert_seventeen(
            "P-H11",
            {
                "vpip": (3, 4),
                "pfr": (1, 4),
                "rfi": (0, 1),
                "open_limp": (1, 1),
                "sb_complete": (0, 0),
                "over_limp": (0, 1),
                "iso_raise": (1, 1),
                "fold_to_iso": (1, 2),
                "call_to_iso": (1, 2),
                "cold_call": (0, 0),
                "four_bet": (0, 0),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (0, 2),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        sb_fold = [
            match
            for match in self._matches("P-H11", "fold_to_iso")
            if match.dimensions["position"] == "SB"
        ][0]
        self.assertTrue(sb_fold.succeeded)
        self.assertEqual(
            dict(sb_fold.dimensions),
            {"position": "SB", "opener_position": "BTN"},
        )
        bb_call = [
            match
            for match in self._matches("P-H11", "call_to_iso")
            if match.succeeded
        ][0]
        self.assertEqual(
            dict(bb_call.dimensions),
            {"position": "BB", "opener_position": "BTN"},
        )

    def test_G06_multiway_iso_call_P_H15(self):
        self._assert_seventeen(
            "P-H15",
            {
                "vpip": (4, 4),
                "pfr": (1, 4),
                "rfi": (0, 1),
                "open_limp": (1, 1),
                "sb_complete": (0, 0),
                "over_limp": (2, 3),
                "iso_raise": (1, 3),
                "fold_to_iso": (2, 3),
                "call_to_iso": (1, 3),
                "cold_call": (0, 0),
                "four_bet": (0, 0),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (0, 3),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        utg_call = [
            match
            for match in self._matches("P-H15", "call_to_iso")
            if match.succeeded
        ][0]
        self.assertEqual(
            dict(utg_call.dimensions),
            {"position": "UTG", "opener_position": "BB"},
        )


class GoldenColdCallTests(PreflopCatalogTestBase):
    """Facing-raise (unraised-before) variants: cold_call vs BTN and CO
    openers (position-vs-position split)."""

    def test_G07_cold_call_btn_opener_P_H1(self):
        self._assert_totals("P-H1", {"cold_call": (1, 2)})

    def test_G08_cold_call_co_opener_P_H10(self):
        self._assert_seventeen(
            "P-H10",
            {
                "vpip": (1, 5),
                "pfr": (1, 5),
                "rfi": (1, 2),
                "open_limp": (0, 2),
                "sb_complete": (0, 0),
                "over_limp": (0, 0),
                "iso_raise": (0, 0),
                "fold_to_iso": (0, 0),
                "call_to_iso": (0, 0),
                "cold_call": (0, 3),
                "four_bet": (0, 0),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (0, 0),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        openers = {
            match.dimensions["opener_position"]
            for match in self._matches("P-H10", "cold_call")
        }
        self.assertEqual(openers, {"CO"})
        rfi = self._matches("P-H10", "rfi")
        self.assertEqual(len(rfi), 2)  # UTG's failed attempt + CO's open
        co_rfi = [match for match in rfi if match.succeeded][0]
        self.assertEqual(dict(co_rfi.dimensions), {"position": "CO"})


class GoldenFourFiveBetTests(PreflopCatalogTestBase):
    """4-bet / 5-bet ladder facing-action variants."""

    def test_G09_four_bet_ladder_P_H6(self):
        self._assert_seventeen(
            "P-H6",
            {
                "vpip": (2, 3),
                "pfr": (2, 3),
                "rfi": (1, 1),
                "open_limp": (0, 1),
                "sb_complete": (0, 0),
                "over_limp": (0, 0),
                "iso_raise": (0, 0),
                "fold_to_iso": (0, 0),
                "call_to_iso": (0, 0),
                "cold_call": (0, 1),
                "four_bet": (1, 2),
                "five_bet": (0, 1),
                "fold_to_four_bet": (1, 1),
                "call_to_four_bet": (0, 1),
                "squeeze": (0, 0),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        four_bet = [
            match for match in self._matches("P-H6", "four_bet") if match.succeeded
        ][0]
        self.assertEqual(four_bet.action_index, 5)  # BTN's 4-bet
        self.assertEqual(dict(four_bet.dimensions), {"position": "BTN"})
        # BB's fold facing the 3-bet (rc == 2, cc == 0) is a four_bet
        # OPPORTUNITY but never a fold_to_squeeze (no limped callers).
        bb_four = [
            match for match in self._matches("P-H6", "four_bet") if not match.succeeded
        ][0]
        self.assertEqual(bb_four.action_index, 4)
        fold_four = self._matches("P-H6", "fold_to_four_bet")[0]
        self.assertTrue(fold_four.succeeded)
        self.assertEqual(
            dict(fold_four.dimensions),
            {"position": "SB", "opener_position": "BTN"},
        )

    def test_G10_five_bet_success_P_H7(self):
        self._assert_seventeen(
            "P-H7",
            {
                "vpip": (2, 3),
                "pfr": (2, 3),
                "rfi": (1, 1),
                "open_limp": (0, 1),
                "sb_complete": (0, 0),
                "over_limp": (0, 0),
                "iso_raise": (0, 0),
                "fold_to_iso": (0, 0),
                "call_to_iso": (0, 0),
                "cold_call": (0, 1),
                "four_bet": (1, 2),
                "five_bet": (1, 1),
                "fold_to_four_bet": (0, 1),
                "call_to_four_bet": (0, 1),
                "squeeze": (0, 0),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        five_bet = self._matches("P-H7", "five_bet")
        self.assertEqual(len(five_bet), 1)
        self.assertTrue(five_bet[0].succeeded)
        self.assertEqual(five_bet[0].action_index, 6)  # SB's 5-bet all-in
        self.assertEqual(dict(five_bet[0].dimensions), {"position": "SB"})
        # BTN's fold facing the 5-bet (rc == 4) matches NOTHING: the
        # 'facing-4bet+' facing label is an aggregation cap only.
        indices = {
            match.action_index
            for match in self._evaluate("P-H7").matches
            if match.actor == "Btn"
        }
        self.assertNotIn(7, indices)

    def test_G11_call_to_four_bet_P_H8(self):
        self._assert_seventeen(
            "P-H8",
            {
                "vpip": (2, 3),
                "pfr": (2, 3),
                "rfi": (1, 1),
                "open_limp": (0, 1),
                "sb_complete": (0, 0),
                "over_limp": (0, 0),
                "iso_raise": (0, 0),
                "fold_to_iso": (0, 0),
                "call_to_iso": (0, 0),
                "cold_call": (0, 1),
                "four_bet": (1, 2),
                "five_bet": (0, 1),
                "fold_to_four_bet": (0, 1),
                "call_to_four_bet": (1, 1),
                "squeeze": (0, 0),
                "fold_to_squeeze": (0, 0),
                "call_to_squeeze": (0, 0),
            },
        )
        sb_call = [
            match
            for match in self._matches("P-H8", "call_to_four_bet")
            if match.succeeded
        ][0]
        self.assertEqual(
            dict(sb_call.dimensions),
            {"position": "SB", "opener_position": "BTN"},
        )
        self.assertEqual(sb_call.action_index, 6)


class GoldenSqueezeTests(PreflopCatalogTestBase):
    """Facing-squeeze variants (squizzer = last preflop raiser)."""

    def test_G12_squeeze_family_P_H9(self):
        self._assert_seventeen(
            "P-H9",
            {
                "vpip": (3, 4),
                "pfr": (2, 4),
                "rfi": (0, 1),
                "open_limp": (1, 1),
                "sb_complete": (0, 0),
                "over_limp": (0, 1),
                "iso_raise": (1, 1),
                "fold_to_iso": (1, 2),
                "call_to_iso": (0, 2),
                "cold_call": (0, 0),
                "four_bet": (0, 2),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (1, 2),
                "fold_to_squeeze": (1, 2),
                "call_to_squeeze": (1, 2),
            },
        )
        squeeze = [
            match for match in self._matches("P-H9", "squeeze") if match.succeeded
        ][0]
        self.assertEqual(squeeze.action_index, 5)  # BB's squeeze
        self.assertEqual(dict(squeeze.dimensions), {"position": "BB"})
        utg_call = [
            match
            for match in self._matches("P-H9", "call_to_squeeze")
            if match.succeeded
        ][0]
        self.assertEqual(
            dict(utg_call.dimensions),
            {"position": "UTG", "opener_position": "BB"},
        )
        btn_fold = [
            match
            for match in self._matches("P-H9", "fold_to_squeeze")
            if match.succeeded
        ][0]
        self.assertEqual(
            dict(btn_fold.dimensions),
            {"position": "BTN", "opener_position": "BB"},
        )

    def test_G13_squeeze_after_iso_P_H12(self):
        self._assert_seventeen(
            "P-H12",
            {
                "vpip": (3, 3),
                "pfr": (1, 3),
                "rfi": (0, 1),
                "open_limp": (1, 1),
                "sb_complete": (0, 0),
                "over_limp": (1, 2),
                "iso_raise": (1, 2),
                "fold_to_iso": (0, 1),
                "call_to_iso": (0, 1),
                "cold_call": (0, 0),
                "four_bet": (0, 2),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (1, 1),
                "fold_to_squeeze": (2, 2),
                "call_to_squeeze": (0, 2),
            },
        )


class VpipPfrGateTests(PreflopCatalogTestBase):
    """First-preflop-decision gate pins (vpip/pfr only)."""

    def test_PIN01_reopened_never_vpip_pfr_P_H13(self):
        self._assert_seventeen(
            "P-H13",
            {
                "vpip": (3, 3),
                "pfr": (2, 3),
                "rfi": (1, 1),
                "open_limp": (0, 1),
                "sb_complete": (0, 0),
                "over_limp": (0, 0),
                "iso_raise": (0, 0),
                "fold_to_iso": (0, 1),
                "call_to_iso": (0, 1),
                "cold_call": (1, 1),
                "four_bet": (0, 2),
                "five_bet": (0, 0),
                "fold_to_four_bet": (0, 0),
                "call_to_four_bet": (0, 0),
                "squeeze": (1, 1),
                "fold_to_squeeze": (2, 2),
                "call_to_squeeze": (0, 2),
            },
        )
        # Every actor has EXACTLY one vpip match (their first decision); the
        # re-opened folds (indices 5, 6) carry none.
        vpip_matches = self._matches("P-H13", "vpip")
        self.assertEqual(
            sorted(match.action_index for match in vpip_matches), [2, 3, 4]
        )
        pfr_indices = sorted(
            match.action_index for match in self._matches("P-H13", "pfr")
        )
        # Three pfr OPPORTUNITIES (one per actor's first decision); SB's
        # CALL (index 3) is an opportunity but never a PFR success.
        self.assertEqual(pfr_indices, [2, 3, 4])
        sb_pfr = [
            match for match in self._matches("P-H13", "pfr") if match.action_index == 3
        ][0]
        self.assertFalse(sb_pfr.succeeded)
        bb_raise = [
            match for match in self._matches("P-H13", "squeeze") if match.succeeded
        ][0]
        self.assertEqual(bb_raise.action_index, 4)
        self.assertEqual(dict(bb_raise.dimensions), {"position": "BB"})

    def test_PIN02_limp_then_raise_never_pfr_P_H12(self):
        # BTN limped (first decision) then re-raised (index 5): the raise is
        # a squeeze success but NEVER a pfr opportunity (documented pin).
        self._assert_totals("P-H12", {"pfr": (1, 3), "vpip": (3, 3)})
        pfr_indices = sorted(
            match.action_index for match in self._matches("P-H12", "pfr")
        )
        self.assertNotIn(5, pfr_indices)
        squeeze = [
            match for match in self._matches("P-H12", "squeeze") if match.succeeded
        ][0]
        self.assertEqual(squeeze.action_index, 5)
        self.assertEqual(dict(squeeze.dimensions), {"position": "BTN"})


class ContinuityTests(PreflopCatalogTestBase):
    """The six pinned stats stay byte-identical to the frozen evaluator."""

    def test_C01_pinned_totals_hand_asserted_P_H1(self):
        self._assert_totals(
            "P-H1",
            {
                "steal": (1, 1),
                "three_bet": (0, 2),
                "fold_to_three_bet": (1, 2),
                "bb_defense": (1, 1),
                "cbet": (1, 1),
                "fold_to_cbet": (1, 1),
            },
        )

    def test_C02_pinned_totals_hand_asserted_P_H10(self):
        self._assert_totals(
            "P-H10",
            {
                "steal": (1, 1),
                "three_bet": (0, 3),
                "fold_to_three_bet": (3, 3),
                "bb_defense": (0, 1),
                "cbet": (0, 0),
                "fold_to_cbet": (0, 0),
            },
        )

    def test_C03_pinned_pairs_match_frozen_evaluator_all_fixtures(self):
        for name in FIXTURES:
            with self.subTest(fixture=name):
                table, button_seat, actions = self._replay_verified(name)
                frozen = self.opportunity.evaluate_hand(
                    table, table.blinds, actions, button_seat=button_seat
                )
                combined = self.catalog.evaluate_preflop_hand(
                    table, table.blinds, actions, button_seat=button_seat
                )
                counts = combined.count_by_stat()
                for stat_id, count in frozen.count_by_stat().items():
                    self.assertEqual(counts[stat_id], count, "%s/%s" % (name, stat_id))

    def test_C04_combined_match_order_pinned_first(self):
        evaluation = self._evaluate("P-H1")
        stat_ids = [match.stat_id for match in evaluation.matches]
        first_catalog = min(
            index for index, stat_id in enumerate(stat_ids) if stat_id in _CATALOG_IDS
        )
        self.assertEqual(
            [stat_id for stat_id in stat_ids[:first_catalog] if stat_id in _PINNED_IDS],
            [stat_id for stat_id in stat_ids[:first_catalog]],
        )


class D5GranularityTests(PreflopCatalogTestBase):
    """D5: one decision point, several stats; never two of the same stat."""

    def test_D01_no_duplicate_stat_id_per_decision_point(self):
        for name in FIXTURES:
            with self.subTest(fixture=name):
                seen = set()
                for match in self._evaluate(name).matches:
                    key = (match.action_index, match.stat_id)
                    self.assertNotIn(key, seen, "%s/%s" % (name, key))
                    seen.add(key)

    def test_D02_one_point_multiple_stats_P_H2(self):
        # SB's complete decision (index 3): sb_complete AND open_limp AND
        # vpip AND rfi opportunities all at one action_index.
        indices = {
            match.action_index
            for match in self._evaluate("P-H2").matches
            if match.actor == "Sb"
        }
        self.assertEqual(indices, {3})


class DeterminismPurityTests(PreflopCatalogTestBase):
    """Z04-Z06: byte-identical re-runs, purity, frozen results."""

    @staticmethod
    def _render(evaluation):
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

    def test_Z04_determinism_byte_identical_rerun(self):
        first = self._evaluate("P-H9")
        second = self._evaluate("P-H9")
        self.assertEqual(self._render(first), self._render(second))
        self.assertEqual(first.matches, second.matches)
        self.assertEqual(first.count_by_stat(), second.count_by_stat())
        self.assertEqual(first.rates(), second.rates())

    def test_Z05_purity_no_sqlalchemy_no_io(self):
        origin = importlib.util.find_spec(self.catalog.__name__).origin
        self.assertIsNotNone(origin)
        with open(origin, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("sqlalchemy", source)
        self.assertNotIn("open(", source)
        self.assertNotIn("Path(", source)

    def test_Z06_frozen_result_dataclasses(self):
        evaluation = self._evaluate("P-H2")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            evaluation.matches = ()
        rates = evaluation.rates()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            rates["vpip"].successes = 99


class BoundaryTests(PreflopCatalogTestBase):
    """B01X-B03X: fail-closed boundaries on the composed API."""

    def test_B01X_button_seat_unknown_raises(self):
        table, _button, actions = FIXTURES["P-H1"]
        with self.assertRaises(ValueError):
            self.catalog.evaluate_preflop_hand(
                table, table.blinds, actions, button_seat=99
            )

    def test_B02X_non_table_raises_type_error(self):
        table, button_seat, actions = FIXTURES["P-H1"]
        with self.assertRaises(TypeError):
            self.catalog.evaluate_preflop_hand(
                "not-a-table", table.blinds, actions, button_seat=button_seat
            )

    def test_B03X_non_blinds_raises_type_error(self):
        table, button_seat, actions = FIXTURES["P-H1"]
        with self.assertRaises(TypeError):
            self.catalog.evaluate_preflop_hand(
                table, "not-blinds", actions, button_seat=button_seat
            )

    def test_B04X_non_action_element_fails_closed(self):
        table, button_seat, actions = FIXTURES["P-H1"]
        with self.assertRaises(TypeError):
            self.catalog.evaluate_preflop_hand(
                table,
                table.blinds,
                list(actions) + ["not-an-action"],
                button_seat=button_seat,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
