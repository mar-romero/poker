"""Binding tests for the deterministic hand state machine (POKER-STATE-001).

Implements the test-designer behaviors B01-B09 (oracle O1-O4, cases C01-C22,
assumptions A1-A9, pins U1-U6) against the planner API freeze.

Pins exercised here (mirrored in ``poker.engine.state`` docstrings):

* ``StateEngine.replay(table, blinds, actions, *, button_seat)`` returns
  ``(list[DecisionSnapshot], StateResult)``; strict replay, never re-sorted
  by sequence (A8 divergence from ``pots.normalize_contributions``).
* ``action_index`` pin (U1): 0-based index of the last applied action; every
  decision snapshot precedes the NEXT action, so its provenance prefix is
  ``actions[:snapshot.action_index + 1]``.
* ``amount_to_call`` is 0 for matched and all-in players (U2), the betting
  round is closed when every active player is all-in, and a terminal fold
  ends the hand with one closing snapshot.
* Blind/ante POST inference per A6 (seat binding + poster order tie-break,
  POST only on PREFLOP); a short blind/ante post is an all-in blind that
  clamps at the remaining stack (U3 pin), any other deviation fails closed.
* Cost-wise: shared action builders are imported from
  ``tests.domain.test_pots`` (A3); fixtures are built locally.
"""

from __future__ import annotations

import importlib.util
import json
import random
import unittest
from dataclasses import FrozenInstanceError

from poker.domain.game import (
    ActionKind,
    AmountSemantics,
    BlindAnteConfig,
    Player,
    SCHEMA_VERSION,
    Seat,
    Street,
    TableMetadata,
)
from poker.domain.money import Chips
from poker.domain.pots import build_pots, normalize_contributions
from poker.domain.pots import effective_stacks as pots_effective_stacks
from poker.engine import rules
from poker.engine.state import (
    STATE_VERSION,
    DecisionSnapshot,
    DuplicateActionError,
    IllegalActionKindError,
    InconsistentHistoryError,
    InsufficientStackError,
    MinRaiseViolationError,
    StateEngine,
    StateResult,
    WrongStreetError,
    WrongTurnError,
)
from tests.domain.test_pots import (
    _act,
    _added,
    _call_added,
    _post,
    _total_to,
    _check,
    _fold,
)

_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "state_version",
        "street",
        "action_index",
        "actor_to_act",
        "legal_actions",
        "active_players",
        "pot",
        "effective_stack",
        "amount_to_call",
        "min_raise",
        "is_betting_round_closed",
    }
)


# ---------------------------------------------------------------------------
# Local fixtures (A3: builders shared from tests.domain.test_pots)
# ---------------------------------------------------------------------------


def _make_table(names, stacks, sb=5, bb=10, ante=0, seat_start=2):
    """Build a TableMetadata whose players occupy ascending seat numbers."""
    players = tuple(
        Player(name, Chips(stack)) for name, stack in zip(names, stacks)
    )
    seats = tuple(Seat(seat_start + index) for index in range(len(names)))
    blinds = BlindAnteConfig(Chips(sb), Chips(bb), Chips(ante))
    table = TableMetadata(players, seats, blinds)
    return table, {name: Chips(stack) for name, stack in zip(names, stacks)}


def _call_total(actor, total, seq, street=Street.PREFLOP):
    return _act(
        actor, street, ActionKind.CALL, Chips(total), AmountSemantics.TOTAL_TO, seq
    )


def _snapshots_json(snapshots):
    return [json.dumps(s.to_dict(), sort_keys=True) for s in snapshots]


def _invariant_tokens(tc, cm, actor, street, expected_tokens):
    tokens = [actor, street, "expected"]
    tokens.extend(expected_tokens if isinstance(expected_tokens, (list, tuple)) else [expected_tokens])
    msg = str(cm.exception).lower()
    for token in tokens:
        tc.assertIn(str(token).lower(), msg)
    return msg


def _walk_leaves(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_leaves(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk_leaves(value)
    else:
        yield node


def _prefix_oracle(tc, actions, stacks, snapshot):
    """O1: re-derive snapshot fields from the provenance prefix using only
    domain/pots.py production functions (normalize_contributions +
    effective_stacks). Two production paths must agree: the engine tracks
    street-scoped in_front from its own strict replay; pots.py re-derives it
    from a sorted stream.
    """
    prefix = list(actions[: snapshot.action_index + 1])
    prefix_street = snapshot.street
    normalized = normalize_contributions(prefix)
    street_front = normalize_contributions(
        [a for a in prefix if a.street is prefix_street]
    )
    top = max((c.value for c in street_front.values()), default=0)
    folded = {a.actor for a in prefix if a.kind is ActionKind.FOLD}
    allin = {
        name
        for name, total in normalized.items()
        if total.value == stacks[name].value
    }
    expected = {
        "pot": sum(c.value for c in normalized.values()),
        "effective_stack": {
            name: stacks[name].value - normalized.get(name, Chips(0)).value
            for name in stacks
        },
        "amount_to_call": {},
        "active_players": tuple(
            name for name in stacks if name not in folded
        ),
    }
    for name in stacks:
        expected["amount_to_call"][name] = (
            0
            if name in folded or name in allin
            else max(top - street_front.get(name, Chips(0)).value, 0)
        )
    return expected


class OracleMixin:
    """Shared O1 assertions for one snapshot."""

    def assertSnapshotMatchesOracle(self, actions, stacks, snapshot):
        expected = _prefix_oracle(self, actions, stacks, snapshot)
        self.assertEqual(snapshot.pot, expected["pot"])
        held = {
            name: snapshot.effective_stack[name].value
            if isinstance(snapshot.effective_stack[name], Chips)
            else snapshot.effective_stack[name]
            for name in expected["effective_stack"]
        }
        self.assertEqual(held, expected["effective_stack"])
        self.assertEqual(
            {
                name: snapshot.amount_to_call[name]
                for name in expected["amount_to_call"]
            },
            expected["amount_to_call"],
        )
        self.assertEqual(snapshot.active_players, expected["active_players"])
        self.assertIn(snapshot.actor_to_act, snapshot.active_players)


def assertSnapshotMatchesOracle(tc, actions, stacks, snapshot):
    tc.assertEqual(snapshot.pot, _prefix_oracle(tc, actions, stacks, snapshot)["pot"])


# ---------------------------------------------------------------------------
# B01 / C01: RED lock probes (import failure is observable lifecycle evidence)
# ---------------------------------------------------------------------------


class EngineModuleProbeTests(unittest.TestCase):
    """C01: both engine modules exist and import cleanly. Before
    implementation this test FILE failed to collect with ModuleNotFoundError
    naming ``poker.engine`` (recorded as the RED artifact in the TDD
    evidence chain)."""

    def test_c01_engine_modules_resolvable(self):
        for module_name in ("poker.engine.state", "poker.engine.rules"):
            with self.subTest(module=module_name):
                spec = importlib.util.find_spec(module_name)
                self.assertIsNotNone(spec)
                self.assertEqual(spec.name, module_name)


# ---------------------------------------------------------------------------
# B02 / C02..C09: strict turn order, heads-up and multiway
# ---------------------------------------------------------------------------


class TurnOrderTests(unittest.TestCase):
    def test_c02_multiway_preflop_order_and_wrong_first_actor(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("UTG", 10, 2),
            _call_added("BTN", 10, 3),
            _call_added("SB", 5, 4),
            _check("BB", 5),
        ]
        snapshots, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        self.assertEqual(
            [s.actor_to_act for s in snapshots], ["UTG", "BTN", "SB", "BB"]
        )
        # U1 pin: 0-based index of the last applied action
        self.assertEqual([s.action_index for s in snapshots], [1, 2, 3, 4])
        self.assertTrue(all(s.street is Street.PREFLOP for s in snapshots))
        # the BB option check closes the round; 4 active players, hand not
        # terminal: no decision point is emitted after the last action
        self.assertEqual(len(snapshots), 4)
        # wrong first voluntary actor -> WrongTurnError naming the expected one
        wrong = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("BTN", 10, 2),
        ]
        with self.assertRaises(WrongTurnError) as cm:
            StateEngine.replay(table, table.blinds, wrong, button_seat=2)
        _invariant_tokens(self, cm, "BTN", "PREFLOP", "UTG")

    def test_c03_postflop_order_skips_folded_seats(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _fold("UTG", 2),
            _call_added("BTN", 10, 3),
            _fold("SB", 4),
            _check("BB", 5),
            _check("BB", 6, street=Street.FLOP),
            _check("BTN", 7, street=Street.FLOP),
        ]
        snapshots, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        flop_actors = [s.actor_to_act for s in snapshots if s.street is Street.FLOP]
        # SB and UTG folded preflop: FLOP order is BB then BTN
        self.assertEqual(flop_actors, ["BB", "BTN"])
        # scenario 2: BTN folded mid-preflop; postflop still starts left of the
        # BTN seat (at the SB position), skipping folded names
        actions2 = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("UTG", 10, 2),
            _fold("BTN", 3),
            _call_added("SB", 5, 4),
            _check("BB", 5),
            _check("SB", 6, street=Street.FLOP),
            _check("BB", 7, street=Street.FLOP),
            _check("UTG", 8, street=Street.FLOP),
        ]
        snapshots2, _ = StateEngine.replay(table, table.blinds, actions2, button_seat=2)
        flop_actors2 = [s.actor_to_act for s in snapshots2 if s.street is Street.FLOP]
        self.assertEqual(flop_actors2, ["SB", "BB", "UTG"])

    def test_c04_heads_up_preflop_swapped_amounts_or_order_fail(self):
        table, stacks = _make_table(["BTN", "OTHER"], [200, 200])
        # swapped amounts: BTN (=SB head-up) posts the bb value first
        with self.assertRaises(InconsistentHistoryError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                [
                    _post("BTN", 10, 0),
                    _post("OTHER", 5, 1),
                ],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "BTN", "PREFLOP", "5")
        self.assertIn("sb", msg)
        # swapped posting order: the non-BTN player posts the sb amount first
        with self.assertRaises(InconsistentHistoryError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                [
                    _post("OTHER", 5, 0),
                    _post("BTN", 10, 1),
                ],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "OTHER", "PREFLOP", "sb")
        self.assertIn("btn", msg)

    def test_c05_heads_up_postflop_order(self):
        table, stacks = _make_table(["BTN", "OTHER"], [200, 200])
        actions = [
            _post("BTN", 5, 0),
            _post("OTHER", 10, 1),
            _call_added("BTN", 5, 2),
            _check("OTHER", 3),
            _check("BTN", 4, street=Street.FLOP),
            _check("OTHER", 5, street=Street.FLOP),
        ]
        snapshots, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        # preflop: BTN/SB acts first once both blinds are posted
        self.assertEqual(
            [s.actor_to_act for s in snapshots][:2], ["BTN", "OTHER"]
        )
        # postflop: BTN (=SB seat) acts first, then the other player
        flop = [s for s in snapshots if s.street is Street.FLOP]
        self.assertEqual([s.actor_to_act for s in flop], ["BTN", "OTHER"])
        self.assertEqual([s.action_index for s in flop], [3, 4])
        # first flop action by the non-BTN player after a completed preflop
        # -> WrongTurnError (BTN pinned as the flop opener)
        wrong = actions[:4] + [_check("OTHER", 7, street=Street.FLOP)]
        with self.assertRaises(WrongTurnError) as cm:
            StateEngine.replay(table, table.blinds, wrong, button_seat=2)
        _invariant_tokens(self, cm, "OTHER", "FLOP", "BTN")

    def test_c06_wrong_turn_error_tokens(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("UTG", 10, 2),
            _call_added("SB", 5, 3),
        ]
        with self.assertRaises(WrongTurnError) as cm:
            StateEngine.replay(table, table.blinds, actions, button_seat=2)
        msg = _invariant_tokens(self, cm, "SB", "PREFLOP", "BTN")
        self.assertIn("utg", msg)  # the player whose turn it actually was

    def test_c07_wrong_street_both_directions(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        posts = [_post("SB", 5, 0), _post("BB", 10, 1)]
        # skip ahead while preflop is unresolved
        skip = posts + [_check("UTG", 2, street=Street.FLOP)]
        with self.assertRaises(WrongStreetError) as cm:
            StateEngine.replay(table, table.blinds, skip, button_seat=2)
        _invariant_tokens(self, cm, "UTG", "PREFLOP", "FLOP")
        # regression after a FLOP action has been applied
        base = posts + [
            _call_added("UTG", 10, 2),
            _call_added("BTN", 10, 3),
            _call_added("SB", 5, 4),
            _check("BB", 5),
            _check("SB", 6, street=Street.FLOP),
        ]
        regression = base + [_check("BB", 7, street=Street.PREFLOP)]
        with self.assertRaises(WrongStreetError) as cm:
            StateEngine.replay(table, table.blinds, regression, button_seat=2)
        _invariant_tokens(self, cm, "BB", "PREFLOP", "FLOP")

    def test_c08_duplicate_action_after_blind_post_and_after_aggression(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        # BB posts the blind then acts again (next in-sequence record, round open)
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _check("BB", 2),
        ]
        with self.assertRaises(DuplicateActionError) as cm:
            StateEngine.replay(table, table.blinds, actions, button_seat=2)
        msg = _invariant_tokens(self, cm, "BB", "PREFLOP", "UTG")
        self.assertIn("duplicate", msg)
        # acting twice before closure, immediately after own aggression
        # (raise over the big blind, then act again in sequence)
        actions2 = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _total_to("UTG", 20, 2),
            _added("UTG", 10, 3),
        ]
        with self.assertRaises(DuplicateActionError) as cm:
            StateEngine.replay(table, table.blinds, actions2, button_seat=2)
        _invariant_tokens(self, cm, "UTG", "PREFLOP", "BTN")

    def test_c09_anti_sort_strict_sequence(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        actions = [
            _post("SB", 5, 5),
            _post("BB", 10, 6),
            _call_added("UTG", 10, 2),
        ]
        with self.assertRaises(InconsistentHistoryError) as cm:
            StateEngine.replay(table, table.blinds, actions, button_seat=2)
        # normalize_contributions would silently sort and succeed here; the
        # engine must NOT re-sort (A8 divergence, planner R6)
        normalized = normalize_contributions(actions)
        self.assertEqual(normalized["UTG"].value, 10)


# ---------------------------------------------------------------------------
# B03 / C10, C13 + the error-class catalog
# ---------------------------------------------------------------------------


class ErrorCatalogTests(unittest.TestCase):
    def test_error_classes_are_value_error_subclasses(self):
        for cls in (
            WrongTurnError,
            WrongStreetError,
            DuplicateActionError,
            InsufficientStackError,
            MinRaiseViolationError,
            IllegalActionKindError,
            InconsistentHistoryError,
        ):
            with self.subTest(cls=cls.__name__):
                self.assertTrue(issubclass(cls, ValueError))

    def test_c10_insufficient_stack_on_chip_actions(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        posts = [_post("SB", 5, 0), _post("BB", 10, 1)]
        limped = posts + [
            _call_added("UTG", 10, 2),
            _call_added("BTN", 10, 3),
            _call_added("SB", 5, 4),
            _check("BB", 5),
        ]
        # (a) flop open bet above the remaining stack (SB has 190 left)
        with self.assertRaises(InsufficientStackError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                limped + [_added("SB", 300, 6, street=Street.FLOP)],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "SB", "FLOP", "190")
        self.assertIn("stack", msg)
        # (b) preflop call above the stack
        with self.assertRaises(InsufficientStackError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                posts + [_call_added("UTG", 500, 2)],
                button_seat=2,
            )
        _invariant_tokens(self, cm, "UTG", "PREFLOP", "200")
        # (c) raise to-total above the stack ceiling
        flop = limped + [_added("SB", 20, 6, street=Street.FLOP)]
        with self.assertRaises(InsufficientStackError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                flop + [_total_to("BB", 400, 7, street=Street.FLOP)],
                button_seat=2,
            )
        _invariant_tokens(self, cm, "BB", "FLOP", "190")

    def test_c13_illegal_kinds_and_closure(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        # POST off preflop
        with self.assertRaises(IllegalActionKindError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                [_post("SB", 5, 0, street=Street.FLOP)],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "SB", "FLOP", "PREFLOP")
        self.assertIn("post", msg)
        posts = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("UTG", 10, 2),
            _call_added("BTN", 10, 3),
            _call_added("SB", 5, 4),
            _check("BB", 5),
            _check("SB", 6, street=Street.FLOP),
            _check("BB", 7, street=Street.FLOP),
            _check("UTG", 8, street=Street.FLOP),
            _check("BTN", 9, street=Street.FLOP),
        ]
        posts_only = posts[:2]
        # BET on a closed flop street does not reopen it
        with self.assertRaises(IllegalActionKindError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                posts + [_added("SB", 10, 10, street=Street.FLOP)],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "SB", "FLOP", "closed")
        self.assertIn("bet", msg)
        # fold by a non-to-act player (open round: UTG is still to act)
        with self.assertRaises(WrongTurnError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                posts_only + [_fold("SB", 2)],
                button_seat=2,
            )
        _invariant_tokens(self, cm, "SB", "PREFLOP", "UTG")


# ---------------------------------------------------------------------------
# B04 / C14..C17: blind/ante post inference
# ---------------------------------------------------------------------------


class BlindInferenceTests(unittest.TestCase):
    def test_c14_ante_batch(self):
        table, stacks = _make_table(
            ["BTN", "SB", "BB", "UTG"], [200] * 4, sb=5, bb=10, ante=2
        )
        actions = [
            _post("BTN", 2, 0),
            _post("SB", 2, 1),
            _post("BB", 2, 2),
            _post("UTG", 2, 3),
            _post("SB", 5, 4),
            _post("BB", 10, 5),
            _call_added("UTG", 10, 6),
        ]
        snapshots, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        first_decision = snapshots[0]
        # the pot includes the four ante posts
        self.assertEqual(first_decision.pot, 4 * 2 + 5 + 10)
        self.assertEqual(first_decision.action_index, 5)
        self.assertEqual(first_decision.actor_to_act, "UTG")
        # O1: stacks reduced by the ante, per-player amounts intact
        expected = _prefix_oracle(self, actions, stacks, first_decision)
        self.assertEqual(first_decision.pot, expected["pot"])
        self.assertEqual(
            {name: first_decision.effective_stack[name] for name in expected["effective_stack"]},
            expected["effective_stack"],
        )
        # 3 ante posts for 4 players -> missing ante count invariant
        bad = [
            _post("BTN", 2, 0),
            _post("SB", 2, 1),
            _post("BB", 2, 2),
            _post("SB", 5, 3),
            _post("BB", 10, 4),
            _call_added("UTG", 10, 5),
        ]
        with self.assertRaises(InconsistentHistoryError) as cm:
            StateEngine.replay(table, table.blinds, bad, button_seat=2)
        msg = _invariant_tokens(self, cm, "UTG", "PREFLOP", ["4", "ante"])
        self.assertIn("inconsistent", msg)

    def test_c15_heads_up_role_binding(self):
        table, stacks = _make_table(["BTN", "OTHER"], [200, 200], sb=5, bb=10)
        # clean: BTN posts sb, other posts bb, BTN acts first preflop
        snapshots, _ = StateEngine.replay(
            table,
            table.blinds,
            [
                _post("BTN", 5, 0),
                _post("OTHER", 10, 1),
                _call_added("BTN", 5, 2),
                _check("OTHER", 3),
            ],
            button_seat=2,
        )
        self.assertEqual(snapshots[0].actor_to_act, "BTN")
        # other posting the sb value again violates the BB expectation
        with self.assertRaises(InconsistentHistoryError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                [
                    _post("BTN", 5, 0),
                    _post("OTHER", 5, 1),
                    _call_added("BTN", 5, 2),
                    _check("OTHER", 3),
                ],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "OTHER", "PREFLOP", "10")
        self.assertIn("bb", msg)

    def test_c16_sb_equal_bb_tie_break(self):
        table, stacks = _make_table(
            ["BTN", "SB", "BB"], [200] * 3, sb=10, bb=10, ante=0
        )
        actions = [
            # FIRST poster (lower sequence) is SB, second is BB
            _post("SB", 10, 0),
            _post("BB", 10, 1),
            _call_added("BTN", 10, 2),
            _check("SB", 3),
            _check("BB", 4),
        ]
        snapshots_1, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        self.assertEqual(snapshots_1[0].actor_to_act, "BTN")
        snapshots_2, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        self.assertEqual(_snapshots_json(snapshots_1), _snapshots_json(snapshots_2))
        # heads-up: FIRST poster is BTN (=SB) and BTN acts first preflop
        table_hu, stacks_hu = _make_table(["BTN", "OTHER"], [200, 200], sb=10, bb=10)
        hu = [
            _post("BTN", 10, 0),
            _post("OTHER", 10, 1),
            _check("BTN", 2),  # sb==bb heads-up: BTN already matched, option check
            _check("OTHER", 3),
        ]
        snapshots_hu, _ = StateEngine.replay(table_hu, table_hu.blinds, hu, button_seat=2)
        self.assertEqual(snapshots_hu[0].actor_to_act, "BTN")
        snapshots_hu2, _ = StateEngine.replay(table_hu, table_hu.blinds, hu, button_seat=2)
        self.assertEqual(_snapshots_json(snapshots_hu), _snapshots_json(snapshots_hu2))

    def test_c17_wrong_post_amount(self):
        table, stacks = _make_table(["BTN", "SB", "BB"], [200] * 3)
        with self.assertRaises(InconsistentHistoryError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                [
                    _post("SB", 7, 0),
                    _post("BB", 10, 1),
                    _call_added("BTN", 10, 2),
                ],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "SB", "PREFLOP", ["7", "5", "10"])
        self.assertIn("inconsistent", msg)


# ---------------------------------------------------------------------------
# B05 / C11, C12, C18: min-raise ladder, all-in reopen boundary, semantics
# ---------------------------------------------------------------------------


class MinRaiseTests(unittest.TestCase):
    def test_c11_ladder_rungs(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [200] * 4)
        base = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("UTG", 10, 2),
            _call_added("BTN", 10, 3),
            _call_added("SB", 5, 4),
            _check("BB", 5),
        ]
        # (a) opening flop bet below one BB (or the remaining stack)
        with self.assertRaises(MinRaiseViolationError) as cm:
            StateEngine.replay(
                table,
                table.blinds,
                base + [_added("SB", 7, 6, street=Street.FLOP)],
                button_seat=2,
            )
        msg = _invariant_tokens(self, cm, "SB", "FLOP", "10")
        self.assertIn("minimum", msg)
        # legal ladder: bet 20 (increment 20 over 0), raise to 40 (increment 20
        # over top 20); a raise to 55 violates the expected 60 raise-to
        # (to-call 40 + minimum increment 20); a raise to 100 is legal and the
        # next raise to 110 violates the new 160 floor.
        ladder_bad = base + [
            _added("SB", 20, 6, street=Street.FLOP),
            _total_to("BB", 40, 7, street=Street.FLOP),
            _total_to("UTG", 55, 8, street=Street.FLOP),
        ]
        with self.assertRaises(MinRaiseViolationError) as cm:
            StateEngine.replay(table, table.blinds, ladder_bad, button_seat=2)
        msg = _invariant_tokens(self, cm, "UTG", "FLOP", ["60", "20", "40"])
        self.assertIn("minimum", msg)
        ladder_ok = base + [
            _added("SB", 20, 6, street=Street.FLOP),
            _total_to("BB", 40, 7, street=Street.FLOP),
            _total_to("UTG", 100, 8, street=Street.FLOP),
        ]
        snapshots, _ = StateEngine.replay(table, table.blinds, ladder_ok, button_seat=2)
        # O2 hand-computed table of the snapshot min_raise ladder
        self.assertEqual(snapshots[4].actor_to_act, "SB")
        self.assertEqual(snapshots[4].min_raise, 10)   # flop opener: one BB
        self.assertEqual(snapshots[5].actor_to_act, "BB")
        self.assertEqual(snapshots[5].min_raise, 40)   # 20 top + 20 increment
        self.assertEqual(snapshots[6].actor_to_act, "UTG")
        self.assertEqual(snapshots[6].min_raise, 60)   # 40 top + 20 increment
        # the BTN-facing floor of 160 after UTG's raise-to-100 surfaces
        # through the violated-raise expectation below
        ladder_hi = ladder_ok + [_total_to("BTN", 110, 9, street=Street.FLOP)]
        with self.assertRaises(MinRaiseViolationError) as cm:
            StateEngine.replay(table, table.blinds, ladder_hi, button_seat=2)
        msg = _invariant_tokens(self, cm, "BTN", "FLOP", ["160", "110", "60"])
        self.assertIn("minimum", msg)

    def test_c12_allin_below_min_does_not_reopen(self):
        # uneven stacks with a flop bet of 40 matched by SB and BB, then the
        # BTN raises all-in to 65 total (increment 25 below the required 40):
        # no reopen - the two matched players get no new decision point.
        table, stacks = _make_table(["BTN", "SB", "BB"], [75, 200, 200])
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("BTN", 10, 2),
            _call_added("SB", 5, 3),
            _check("BB", 4),
            _added("SB", 40, 5, street=Street.FLOP),
            _call_added("BB", 40, 6, street=Street.FLOP),
            _total_to("BTN", 65, 7, street=Street.FLOP),  # all-in, increment 25
        ]
        snapshots, result = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        self.assertEqual(len(snapshots), 6)
        self.assertEqual(snapshots[-1].action_index, 6)
        self.assertEqual(snapshots[-1].actor_to_act, "BTN")
        # no decision point for the matched players after the all-in
        self.assertTrue(all(s.action_index < 7 for s in snapshots))
        # the all-in excess of 25 is uncalled and absent from the pots, exactly
        # as build_pots computes it (O4)
        pots_result = build_pots(actions, stacks)
        self.assertEqual(result.contributions, pots_result.contributions)
        self.assertEqual(result.uncalled, pots_result.uncalled)
        self.assertEqual(result.pots, pots_result)

    def test_c12b_allin_at_min_reeopens(self):
        # same ladder shape, but the BTN all-in raise carries increment 40:
        # betting reopens and SB/BB act again with {FOLD, CALL, RAISE}.
        table, stacks = _make_table(["BTN", "SB", "BB"], [90, 200, 200])
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("BTN", 10, 2),
            _call_added("SB", 5, 3),
            _check("BB", 4),
            _added("SB", 40, 5, street=Street.FLOP),
            _call_added("BB", 40, 6, street=Street.FLOP),
            _total_to("BTN", 80, 7, street=Street.FLOP),  # all-in, increment 40
            _call_added("SB", 40, 8, street=Street.FLOP),
            _call_added("BB", 40, 9, street=Street.FLOP),
        ]
        snapshots, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        reopened = [s for s in snapshots if s.action_index >= 7]
        self.assertEqual([s.actor_to_act for s in reopened], ["SB", "BB"])
        self.assertIn(ActionKind.FOLD, reopened[0].legal_actions)
        self.assertIn(ActionKind.CALL, reopened[0].legal_actions)
        self.assertIn(ActionKind.RAISE, reopened[0].legal_actions)

    def test_c18_amount_semantics_equivalence(self):
        table, stacks = _make_table(["BTN", "SB", "BB"], [400] * 3, sb=5, bb=10)
        prefix = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("BTN", 10, 2),
            _call_added("SB", 5, 3),
            _check("BB", 4),
        ]
        # encoding A: ADDED increments
        added_version = prefix + [
            _added("SB", 20, 5, street=Street.FLOP),
            _total_to("BB", 40, 6, street=Street.FLOP),
            _total_to("BTN", 100, 7, street=Street.FLOP),
            _call_added("SB", 80, 8, street=Street.FLOP),
            _call_added("BB", 60, 9, street=Street.FLOP),
        ]
        # encoding B: equivalent TOTAL_TO street totals (opening bet carries
        # kind BET with a TOTAL_TO street-total; raises stay kind RAISE)
        total_version = prefix + [
            _act(
                "SB",
                Street.FLOP,
                ActionKind.BET,
                Chips(20),
                AmountSemantics.TOTAL_TO,
                5,
            ),
            _total_to("BB", 40, 6, street=Street.FLOP),
            _total_to("BTN", 100, 7, street=Street.FLOP),
            _call_total("SB", 100, 8, street=Street.FLOP),
            _call_total("BB", 100, 9, street=Street.FLOP),
        ]
        snap_a, result_a = StateEngine.replay(table, table.blinds, added_version, button_seat=2)
        snap_b, result_b = StateEngine.replay(table, table.blinds, total_version, button_seat=2)
        self.assertEqual(_snapshots_json(snap_a), _snapshots_json(snap_b))
        self.assertEqual(result_a.to_dict(), result_b.to_dict())
        # TOTAL_TO below the chips already in front -> impossible history
        table_hu, stacks_hu = _make_table(["BTN", "OTHER"], [200, 200], sb=5, bb=10)
        with self.assertRaises(InconsistentHistoryError) as cm:
            StateEngine.replay(
                table_hu,
                table_hu.blinds,
                [
                    _post("BTN", 5, 0),
                    _post("OTHER", 10, 1),
                    _call_total("BTN", 3, 2),
                ],
                button_seat=2,
            )
        _invariant_tokens(self, cm, "BTN", "PREFLOP", ["3", "5"])


# ---------------------------------------------------------------------------
# B06 / C19: byte stability, immutability, exact keys
# ---------------------------------------------------------------------------


class SnapshotSerializationTests(unittest.TestCase):
    def _three_player_actions(self):
        return [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("BTN", 10, 2),
            _call_added("SB", 5, 3),
            _check("BB", 4),
            _check("SB", 5, street=Street.FLOP),
            _check("BB", 6, street=Street.FLOP),
            _check("BTN", 7, street=Street.FLOP),
        ]

    def test_c19_byte_stability_immutability_exact_keys(self):
        table, stacks = _make_table(["BTN", "SB", "BB"], [300] * 3)
        actions = self._three_player_actions()
        snapshots_1, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        snapshots_2, _ = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        # replay twice -> byte-identical whole list (O3)
        self.assertEqual(_snapshots_json(snapshots_1), _snapshots_json(snapshots_2))
        first = snapshots_1[0]
        for snapshot in snapshots_1:
            payload = snapshot.to_dict()
            self.assertEqual(frozenset(payload), _SNAPSHOT_KEYS)
            self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
            self.assertEqual(payload["state_version"], STATE_VERSION)
            # A2: enum names, plain ints/strings/bools only, never floats (O3)
            for leaf in _walk_leaves(payload):
                self.assertIsInstance(leaf, (int, str))
                self.assertNotIsInstance(leaf, float)
            # from_dict round-trip is equal
            self.assertEqual(DecisionSnapshot.from_dict(payload), snapshot)
        # frozen instances
        with self.assertRaises(FrozenInstanceError):
            first.pot = 1
        # mapping fields are immutable proxies
        with self.assertRaises(TypeError):
            first.effective_stack["BTN"] = 1
        with self.assertRaises(TypeError):
            first.amount_to_call["BTN"] = 1
        # exact-key / version rejection
        missing = first.to_dict()
        del missing["min_raise"]
        with self.assertRaises(ValueError):
            DecisionSnapshot.from_dict(missing)
        extra = first.to_dict()
        extra["board"] = []
        with self.assertRaises(ValueError):
            DecisionSnapshot.from_dict(extra)
        wrong_schema = first.to_dict()
        wrong_schema["schema_version"] = 2
        with self.assertRaises(ValueError):
            DecisionSnapshot.from_dict(wrong_schema)
        wrong_state = first.to_dict()
        wrong_state["state_version"] = "0.9.9"
        with self.assertRaises(ValueError):
            DecisionSnapshot.from_dict(wrong_state)


# ---------------------------------------------------------------------------
# B07 / C20: full-field snapshot oracle + final build_pots matching
# ---------------------------------------------------------------------------


class SnapshotContentTests(unittest.TestCase):
    def assertFullFieldSnapshot(self, actions, stacks, snapshot):
        # pot + per-player effective_stack + amount_to_call come from the pots
        # oracle (O1)
        expected = _prefix_oracle(self, actions, stacks, snapshot)
        self.assertEqual(snapshot.pot, expected["pot"])
        self.assertEqual(
            {
                name: snapshot.effective_stack[name]
                for name in expected["effective_stack"]
            },
            expected["effective_stack"],
        )
        self.assertEqual(
            {
                name: snapshot.amount_to_call[name]
                for name in expected["amount_to_call"]
            },
            expected["amount_to_call"],
        )
        self.assertEqual(snapshot.active_players, expected["active_players"])
        self.assertIn(snapshot.actor_to_act, snapshot.active_players)
        # A7 pinned legal-actions set
        to_call = snapshot.amount_to_call[snapshot.actor_to_act]
        kinds = set(snapshot.legal_actions)
        self.assertTrue(snapshot.legal_actions)
        if to_call > 0:
            self.assertIn(ActionKind.FOLD, kinds)
            self.assertIn(ActionKind.CALL, kinds)
        else:
            self.assertIn(ActionKind.CHECK, kinds)

    def test_c20_three_player_mixed_ladder(self):
        table, stacks = _make_table(["BTN", "SB", "BB"], [400, 300, 150])
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _call_added("BTN", 10, 2),
            _call_added("SB", 5, 3),
            _check("BB", 4),
            _added("SB", 20, 5, street=Street.FLOP),
            _total_to("BB", 40, 6, street=Street.FLOP),
            _total_to("BTN", 100, 7, street=Street.FLOP),
            _call_added("SB", 80, 8, street=Street.FLOP),
            _fold("BB", 9, street=Street.FLOP),
        ]
        snapshots, result = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        self.assertEqual(len(snapshots), 8)
        # staged ordering assertion (A7): facing a bet with room to raise
        staged = snapshots[4]
        self.assertEqual(staged.action_index, 5)
        self.assertEqual(staged.actor_to_act, "BB")
        self.assertEqual(
            staged.legal_actions, (ActionKind.FOLD, ActionKind.CALL, ActionKind.RAISE)
        )
        for snapshot in snapshots:
            with self.subTest(action_index=snapshot.action_index):
                self.assertFullFieldSnapshot(actions, stacks, snapshot)
        # O4: final StateResult equals the build_pots pipeline
        pots_result = build_pots(actions, stacks)
        self.assertEqual(result.pots, pots_result)
        self.assertEqual(result.uncalled, pots_result.uncalled)
        self.assertEqual(result.contributions, pots_result.contributions)

    def test_c20b_four_player_allin_spread(self):
        table, stacks = _make_table(["BTN", "SB", "BB", "UTG"], [500, 300, 150, 100])
        actions = [
            _post("SB", 5, 0),
            _post("BB", 10, 1),
            _total_to("UTG", 100, 2),    # all-in
            _call_added("BTN", 100, 3),
            _total_to("SB", 290, 4),     # leaves 10 behind
            _call_added("BB", 140, 5),   # all-in short call
            _call_added("BTN", 190, 6),  # completes to the 290 top
        ]
        snapshots, result = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        self.assertEqual(len(snapshots), 5)
        for snapshot in snapshots:
            with self.subTest(action_index=snapshot.action_index):
                self.assertFullFieldSnapshot(actions, stacks, snapshot)
        pots_result = build_pots(actions, stacks)
        self.assertEqual(result.pots, pots_result)
        self.assertEqual(result.uncalled, pots_result.uncalled)
        self.assertEqual(result.contributions, pots_result.contributions)
        self.assertEqual(result.pots.effective_stacks, pots_result.effective_stacks)


# ---------------------------------------------------------------------------
# B08 / C21: seeded property oracle (seeds 1..20)
# ---------------------------------------------------------------------------

_BET_SIZES = (...)


class PropertyOracleTests(unittest.TestCase):
    """Conservative legal-only generator, independent of poker.engine.rules; O1
    asserted per decision point, byte-stability per seed rerun, final
    StateResult matched against build_pots (O4)."""

    @staticmethod
    def _generate_engine_scenario(seed):
        rng = random.Random(seed)
        count = rng.choice([2, 2, 3, 4, 5])
        names = [f"P{i}" for i in range(count)]
        bb = rng.choice([2, 4, 5, 10])
        sb = rng.choice([v for v in (1, 2, 3, 4, 5, 6, 8, 10) if v <= bb])
        stacks_value = {name: rng.randint(bb * 3, 300) for name in names}
        table, stacks = _make_table(
            names, [stacks_value[name] for name in names], sb=sb, bb=bb
        )
        seat_numbers = [seat.number for seat in table.seats]
        button_pos = rng.randrange(count)
        button_seat = seat_numbers[button_pos]
        if count == 2:
            sb_pos, bb_pos = button_pos, (button_pos + 1) % 2
        else:
            sb_pos = (button_pos + 1) % count
            bb_pos = (button_pos + 2) % count

        actions = []
        seq = 0
        contributions = {name: 0 for name in names}
        street_front = {name: 0 for name in names}
        folded = set()
        allin = set()
        acted = set()
        top = 0
        maxinc = bb

        for pos in (sb_pos, bb_pos):
            actions.append(_post(names[pos], sb if pos is sb_pos else bb, seq))
            seq += 1
        contributions[names[sb_pos]] += sb
        street_front[names[sb_pos]] = sb
        contributions[names[bb_pos]] += bb
        street_front[names[bb_pos]] = bb
        top = bb

        streets_plan = [Street.PREFLOP, Street.FLOP, Street.TURN][: rng.randint(1, 3)]
        hand_over = False

        for street_index, street in enumerate(streets_plan):
            if hand_over:
                break
            if street_index > 0:
                top = 0
                maxinc = 0
                street_front = {name: 0 for name in names}
                acted = set()
            active = [name for name in names if name not in folded]
            if len(active) <= 1 or all(name in allin for name in active):
                break
            # rotation start: preflop after BB (strictly), postflop at SB seat
            if street is Street.PREFLOP:
                start = (bb_pos + 1) % count
            else:
                start = sb_pos
            position = start
            for _ in range(count * count):
                actor = None
                for step in range(count):
                    name = names[(position + step) % count]
                    if name in folded or name in allin or name in acted:
                        continue
                    actor = name
                    break
                if actor is None:
                    break  # street closed
                position = (names.index(actor) + 1) % count
                front = street_front[actor]
                remaining = stacks_value[actor] - contributions[actor]
                to_call = max(top - front, 0)
                if to_call > 0:
                    roll = rng.random()
                    if roll < 0.15:
                        actions.append(_fold(actor, seq, street))
                        acted.add(actor)
                        folded.add(actor)
                    elif roll < 0.7 or remaining <= to_call:
                        call_amount = min(to_call, remaining)
                        actions.append(_call_added(actor, call_amount, seq, street))
                        contributions[actor] += call_amount
                        street_front[actor] = front + call_amount
                        acted.add(actor)
                        if contributions[actor] == stacks_value[actor]:
                            allin.add(actor)
                    else:
                        needed = to_call + max(maxinc, bb)
                        if remaining >= needed and rng.random() < 0.75:
                            bonus = rng.randint(0, remaining - needed)
                            delta = needed + bonus
                        else:
                            delta = remaining  # all-in raise (legal exception)
                        actions.append(
                            _act(
                                actor,
                                street,
                                ActionKind.RAISE,
                                Chips(delta),
                                AmountSemantics.ADDED,
                                seq,
                            )
                        )
                        contributions[actor] += delta
                        new_top = front + delta
                        increment = new_top - top
                        if increment >= max(maxinc, bb):
                            acted = {actor}
                            maxinc = max(maxinc, increment)
                        else:
                            acted.add(actor)  # sub-min all-in: no reopen
                        top = new_top
                        street_front[actor] = new_top
                        if contributions[actor] == stacks_value[actor]:
                            allin.add(actor)
                else:
                    roll = rng.random()
                    if top == 0:
                        if remaining < bb or roll < 0.7:
                            if rng.random() >= 0.5 and remaining >= bb:
                                pass
                            if remaining < bb:
                                actions.append(_added(actor, remaining, seq, street))
                                contributions[actor] += remaining
                                street_front[actor] = front + remaining
                                acted = {actor}
                                top = front + remaining
                                if contributions[actor] == stacks_value[actor]:
                                    allin.add(actor)
                            else:
                                actions.append(_check(actor, seq, street))
                                acted.add(actor)
                        else:
                            options = [bb]
                            options.extend(
                                size for size in (bb * 2, bb * 3) if size <= remaining
                            )
                            amount = rng.choice(options)
                            actions.append(_added(actor, amount, seq, street))
                            contributions[actor] += amount
                            street_front[actor] = front + amount
                            acted = {actor}
                            top = front + amount
                            maxinc = max(maxinc, amount)
                    else:
                        # to_call==0 with a live top (preflop BB option / venues)
                        needed = top + max(maxinc, bb)
                        delta_needed = needed - front
                        if delta_needed <= remaining and rng.random() < 0.5:
                            bonus = rng.randint(0, remaining - delta_needed)
                            delta = delta_needed + bonus
                            actions.append(
                                _act(
                                    actor,
                                    street,
                                    ActionKind.RAISE,
                                    Chips(delta),
                                    AmountSemantics.ADDED,
                                    seq,
                                )
                            )
                            contributions[actor] += delta
                            new_top = front + delta
                            increment = new_top - top
                            if increment >= max(maxinc, bb):
                                acted = {actor}
                                maxinc = max(maxinc, increment)
                            else:
                                acted.add(actor)
                            top = new_top
                            street_front[actor] = new_top
                            if contributions[actor] == stacks_value[actor]:
                                allin.add(actor)
                        else:
                            actions.append(_check(actor, seq, street))
                            acted.add(actor)
                seq += 1
                active_left = [name for name in names if name not in folded]
                if len(active_left) <= 1 or all(
                    name in allin for name in active_left
                ):
                    hand_over = True
                    break
        return table, stacks, actions, button_seat

    def test_c21_seeded_property_oracle(self):
        for seed in range(1, 21):
            with self.subTest(seed=seed):
                table, stacks, actions, button_seat = self._generate_engine_scenario(seed)
                snapshots, result = StateEngine.replay(
                    table, table.blinds, actions, button_seat=button_seat
                )
                for snapshot in snapshots:
                    if snapshot.actor_to_act is None:
                        # terminal snapshot: closed hand, no pending actor
                        self.assertTrue(snapshot.is_betting_round_closed)
                        self.assertEqual(snapshot.legal_actions, ())
                        continue
                    expected = _prefix_oracle(self, actions, stacks, snapshot)
                    self.assertEqual(snapshot.pot, expected["pot"])
                    self.assertEqual(
                        {
                            name: snapshot.effective_stack[name]
                            for name in expected["effective_stack"]
                        },
                        expected["effective_stack"],
                    )
                    self.assertEqual(
                        {
                            name: snapshot.amount_to_call[name]
                            for name in expected["amount_to_call"]
                        },
                        expected["amount_to_call"],
                    )
                    self.assertEqual(
                        snapshot.active_players, expected["active_players"]
                    )
                    self.assertIn(
                        snapshot.actor_to_act, snapshot.active_players
                    )
                    # A7: full legal_actions under any to-call value
                    kinds = set(snapshot.legal_actions)
                    self.assertTrue(kinds)
                    if snapshot.amount_to_call[snapshot.actor_to_act] > 0:
                        self.assertIn(ActionKind.CALL, kinds)
                        self.assertIn(ActionKind.FOLD, kinds)
                    else:
                        self.assertIn(ActionKind.CHECK, kinds)
                snapshots_rerun, result_rerun = StateEngine.replay(
                    table, table.blinds, actions, button_seat=button_seat
                )
                self.assertEqual(
                    _snapshots_json(snapshots), _snapshots_json(snapshots_rerun)
                )
                self.assertEqual(result.to_dict(), result_rerun.to_dict())
                pots_result = build_pots(actions, stacks)
                self.assertEqual(result.pots, pots_result)
                self.assertEqual(result.uncalled, pots_result.uncalled)
                self.assertEqual(result.contributions, pots_result.contributions)


# ---------------------------------------------------------------------------
# B09 / C22: heads-up fold walk with uncalled return
# ---------------------------------------------------------------------------


class HeadsUpEndToEndTests(unittest.TestCase):
    def test_c22_heads_up_fold_walk(self):
        table, stacks = _make_table(["BTN", "BB"], [200, 200], sb=5, bb=10)
        actions = [
            _post("BTN", 5, 0),
            _post("BB", 10, 1),
            _fold("BTN", 2, street=Street.PREFLOP),
        ]
        snapshots, result = StateEngine.replay(table, table.blinds, actions, button_seat=2)
        # no decision point after the fold; the only snapshot after the posts
        # is the terminal close
        self.assertEqual(len(snapshots), 2)
        login = snapshots[-1]
        self.assertEqual(login.action_index, 2)
        self.assertTrue(login.is_betting_round_closed)
        self.assertIsNone(login.actor_to_act)
        self.assertEqual(login.legal_actions, ())
        self.assertEqual(login.active_players, ("BB",))
        # StateResult: contested level sb; uncalled bb-sb returned to BB,
        # exactly as build_pots computes it (O4)
        pots_result = build_pots(actions, stacks)
        self.assertEqual(result.pots, pots_result)
        self.assertEqual(result.uncalled, pots_result.uncalled)
        self.assertEqual(result.contributions, pots_result.contributions)
        self.assertEqual(sum(pot.total.value for pot in result.pots.pots), 10)
        self.assertEqual(result.uncalled[0].player, "BB")
        self.assertEqual(result.uncalled[0].amount.value, 5)
        # conservation algebra on the exposed final state
        contrib_total = sum(c.value for c in result.contributions.values())
        called_total = sum(pot.total.value for pot in result.pots.pots)
        returned_total = sum(u.amount.value for u in result.uncalled)
        self.assertEqual(contrib_total, called_total + returned_total)


if __name__ == "__main__":
    unittest.main()
