"""Contract tests for the normalized hand-history ingest contract.

Covers POKER-INGEST-CONTRACT-001 / PLAN-S1:

1. ``CONTRACT_VERSION`` is the pinned semver string ``'1.0.0'``.
2. The pinned ``ParseResult`` validity matrix (errors empty => valid;
   errors non-empty => no hands; hands + warnings + no errors = partial
   success; empty hands + empty errors = no hands found, valid).
3. Warnings and errors are separate lists and are never merged.
4. ``ParsedHand`` structural validation (button seat, action ordering and
   types, seat count bounds via ``TableMetadata``).
5. Exact-key ``to_dict`` -> ``from_dict`` round-trip that is byte-stable
   across cycles and rejects extra/missing keys and wrong schema version.
6. No floats (and no sets) anywhere in serialized payloads.
7. ``RawSourceRef`` provenance (source name + SHA-256 hex digest).
8. ``HandAdapter`` is a ``runtime_checkable`` protocol.
9. Synthetic in-test fixtures: a parseable minimum-seat cash hand and a
   parseable maximum-seat cash hand, plus truncated raw text that yields
   errors and no hands (never synthesized actions).

Only the standard library and the canonical domain modules are used, so the
suite runs with plain ``unittest`` from the repository/worktree root.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from dataclasses import FrozenInstanceError
from typing import runtime_checkable

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
from poker.ingest import contracts
from poker.ingest.contracts import (
    CONTRACT_VERSION,
    HandAdapter,
    ParseIssue,
    ParsedHand,
    ParseResult,
    RawSourceRef,
)

VALIDATION_ERRORS = (ValueError, TypeError)

_SUMMARY_MARKER = "*** SUMMARY ***"

_TWO_SEAT_RAW = (
    "synthetic-room Hand #HH-000001: Hold'em No Limit ($5/$10 USD)\n"
    "Seat 2: player0 ($20000)\n"
    "Seat 3: player1 ($20000)\n"
    "player0: folds\n"
    "player1: folds\n"
    "*** SUMMARY ***\n"
)

_MAX_SEAT_RAW = (
    "synthetic-room Hand #HH-000002: Hold'em No Limit ($5/$10 USD)\n"
    "Seat 2: player0 ($20000)\n"
    "Seat 3: player1 ($20000)\n"
    "Seat 4: player2 ($20000)\n"
    "Seat 5: player3 ($20000)\n"
    "Seat 6: player4 ($20000)\n"
    "Seat 7: player5 ($20000)\n"
    "Seat 8: player6 ($20000)\n"
    "Seat 9: player7 ($20000)\n"
    "Seat 10: player8 ($20000)\n"
    "player0: folds\n"
    "player8: folds\n"
    "*** SUMMARY ***\n"
)

_TRUNCATED_RAW = (
    "synthetic-room Hand #HH-000009: Hold'em No Limit ($5/$10 USD)\n"
    "Seat 2: player0 ($20000\n"
)

_SEAT_LINE = re.compile(r"^Seat (\d+): (\S+) \(\$(\d+)\)$")
_FOLD_LINE = re.compile(r"^(\S+): folds$")


def _table(player_count: int = 2) -> TableMetadata:
    """Minimum/maximum legal cash table: one seat per player, seats 2..10."""
    players = tuple(Player(f"player{i}", Chips(20000)) for i in range(player_count))
    seats = tuple(Seat(number) for number in range(2, 2 + player_count))
    return TableMetadata(players, seats, BlindAnteConfig(Chips(5), Chips(10), Chips(0)))


def _fold(sequence: int, actor: str) -> Action:
    return Action(actor, Street.PREFLOP, ActionKind.FOLD, None, None, sequence, "synthetic")


def _hand(**overrides) -> ParsedHand:
    fields = {
        "table": _table(2),
        "actions": (_fold(0, "player0"), _fold(1, "player1")),
        "button_seat": 2,
        "hand_number": "HH-000001",
        "table_name": "synthetic-room Hold'em",
        "street_reached": 0,
    }
    fields.update(overrides)
    return ParsedHand(**fields)


def _source() -> RawSourceRef:
    return RawSourceRef(
        source_name="synthetic-room",
        source_sha256=hashlib.sha256(b"raw hand bytes").hexdigest(),
    )


def _warning() -> ParseIssue:
    return ParseIssue(kind="warning", code="ignored-line", message="unrecognized line skipped")


def _error() -> ParseIssue:
    return ParseIssue(kind="error", code="truncated-hand", message="hand text ends mid-statement")


def _assert_payload_safe(node: object) -> None:
    """No floats and no sets anywhere in a serialized payload (PLAN-S1 item 6)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise AssertionError(f"payload key is not a string: {key!r}")
            _assert_payload_safe(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _assert_payload_safe(item)
    elif isinstance(node, float):
        raise AssertionError(f"serialized payload contains a float: {node!r}")
    elif isinstance(node, (set, frozenset)):
        raise AssertionError("serialized payload contains a set")


class _SyntheticAdapter:
    """Minimal synthetic fixture adapter (PLAN-S1 item 9).

    Demonstrates the ``HandAdapter`` protocol end to end on in-test fixture
    text. It recognizes seat lines, fold lines and the summary marker only;
    anything unparseable or truncated produces errors and zero hands — it
    never synthesizes actions that the raw text does not contain.
    """

    name = "synthetic-fixture"
    CONTRACT_VERSION = CONTRACT_VERSION

    def parse(self, raw_text: str, source_name: str) -> ParseResult:
        source = RawSourceRef(
            source_name=source_name,
            source_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        )
        players: list[tuple[int, str, int]] = []
        actions: list[Action] = []
        errors: list[ParseIssue] = []
        for line in raw_text.splitlines():
            seat_match = _SEAT_LINE.fullmatch(line)
            if seat_match:
                players.append(
                    (int(seat_match.group(1)), seat_match.group(2), int(seat_match.group(3)))
                )
                continue
            fold_match = _FOLD_LINE.fullmatch(line)
            if fold_match:
                actions.append(_fold(len(actions), fold_match.group(1)))
        if _SUMMARY_MARKER not in raw_text:
            errors.append(
                ParseIssue(
                    kind="error",
                    code="truncated-hand",
                    message="hand text ends before the summary marker",
                )
            )
        if errors:
            # Never synthesize actions/hands for unparseable input.
            return ParseResult(hands=(), warnings=(), errors=tuple(errors), source=source)
        table = TableMetadata(
            tuple(Player(name, Chips(stack)) for _, name, stack in players),
            tuple(Seat(number) for number, _, _ in players),
            BlindAnteConfig(Chips(5), Chips(10), Chips(0)),
        )
        hand = ParsedHand(
            table=table,
            actions=tuple(actions),
            button_seat=2,
            hand_number="synthetic",
            table_name="synthetic-room Hold'em",
            street_reached=0,
        )
        return ParseResult(hands=(hand,), warnings=(), errors=(), source=source)


class ContractVersionTests(unittest.TestCase):
    def test_contract_version_is_pinned_semver_string(self) -> None:
        self.assertEqual(CONTRACT_VERSION, "1.0.0")
        self.assertIsInstance(CONTRACT_VERSION, str)
        self.assertIn("CONTRACT_VERSION", contracts.__all__)


class ParseResultValidityMatrixTests(unittest.TestCase):
    """Pinned validity matrix (PLAN-S1 item 2)."""

    def test_hands_without_errors_are_valid(self) -> None:
        result = ParseResult(hands=(_hand(),), warnings=(), errors=(), source=_source())
        self.assertTrue(result.ok)
        self.assertEqual(len(result.hands), 1)
        self.assertEqual(result.errors, ())

    def test_hands_with_warnings_and_no_errors_is_partial_success(self) -> None:
        result = ParseResult(
            hands=(_hand(),), warnings=(_warning(),), errors=(), source=_source()
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.hands), 1)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.errors, ())

    def test_no_hands_no_errors_is_valid_zero_hands_found(self) -> None:
        result = ParseResult(hands=(), warnings=(), errors=(), source=_source())
        self.assertTrue(result.ok)
        self.assertEqual(len(result.hands), 0)

    def test_errors_non_empty_requires_no_hands(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            ParseResult(hands=(_hand(),), warnings=(), errors=(_error(),), source=_source())

    def test_errors_only_result_is_not_ok_and_has_no_hands(self) -> None:
        result = ParseResult(hands=(), warnings=(), errors=(_error(),), source=_source())
        self.assertFalse(result.ok)
        self.assertEqual(len(result.hands), 0)
        self.assertEqual(len(result.errors), 1)


class WarningsErrorsSeparationTests(unittest.TestCase):
    """Warnings and errors are separate lists, never merged (PLAN-S1 item 3)."""

    def test_warning_and_error_lists_stay_separate(self) -> None:
        result = ParseResult(
            hands=(), warnings=(_warning(),), errors=(_error(),), source=_source()
        )
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIsNot(result.warnings, result.errors)
        self.assertEqual(result.warnings[0].kind, "warning")
        self.assertEqual(result.errors[0].kind, "error")
        self.assertNotIn(_warning(), result.errors)
        self.assertNotIn(_error(), result.warnings)

    def test_issue_kind_is_restricted_and_hand_warnings_stay_on_the_hand(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            ParseIssue(kind="info", code="x", message="not a warning or error")
        with self.assertRaises(VALIDATION_ERRORS):
            _hand(warnings=(_error(),))  # error-kind issue never lives on a hand
        hand = _hand(warnings=(_warning(),))
        result = ParseResult(hands=(hand,), warnings=(), errors=(), source=_source())
        self.assertEqual(len(hand.warnings), 1)  # retained on the hand (AC2)
        self.assertEqual(result.warnings, ())  # not merged into result warnings


class ParsedHandValidationTests(unittest.TestCase):
    """Structural validation (PLAN-S1 item 4)."""

    def test_button_seat_bounds_2_to_10(self) -> None:
        _hand(button_seat=2)
        _hand(button_seat=3)
        for bad in (1, 0, 11, -1, 2.0, "2"):
            with self.assertRaises(VALIDATION_ERRORS):
                _hand(button_seat=bad)

    def test_button_seat_must_be_an_occupied_seat(self) -> None:
        # DB alignment: hands.button_seat is a deferred composite FK to
        # hand_players(hand_id, seat_number), so an empty seat is rejected.
        with self.assertRaises(VALIDATION_ERRORS):
            _hand(button_seat=10, table=_table(2))

    def test_actions_must_be_action_instances_strictly_increasing_no_duplicates(
        self,
    ) -> None:
        _hand(actions=(_fold(0, "player0"), _fold(1, "player1"), _fold(2, "player0")))
        with self.assertRaises(VALIDATION_ERRORS):
            _hand(actions=(_fold(0, "player0"), "fold"))  # non-Action element
        with self.assertRaises(VALIDATION_ERRORS):
            _hand(actions=(_fold(0, "player0"), _fold(0, "player1")))  # duplicate
        with self.assertRaises(VALIDATION_ERRORS):
            _hand(actions=(_fold(1, "player0"), _fold(0, "player1")))  # decreasing

    def test_seat_count_bounds_2_to_10_via_table_metadata(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            _hand(table=_table(1))  # heads-up minimum is 2 seats
        _hand(table=_table(2))
        # Maximum seat count the canonical domain can express: Seat numbers
        # are constrained to 2..10, so a full table is 9 seats (see spec).
        _hand(table=_table(9))

    def test_street_reached_bounds_0_to_3(self) -> None:
        for value in (0, 1, 2, 3):
            _hand(street_reached=value)
        for bad in (4, -1, 1.0):
            with self.assertRaises(VALIDATION_ERRORS):
                _hand(street_reached=bad)

    def test_parsed_hand_is_frozen(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            _hand().street_reached = 3


class SerializationTests(unittest.TestCase):
    """Exact-key round-trip, byte stability, no floats/sets (PLAN-S1 items 5+6)."""

    def test_round_trip_is_exact_key_byte_stable(self) -> None:
        hand = _hand(
            actions=(
                _fold(0, "player0"),
                Action(
                    "player1",
                    Street.PREFLOP,
                    ActionKind.CALL,
                    Chips(10),
                    AmountSemantics.ADDED,
                    1,
                    "synthetic",
                ),
            ),
            warnings=(_warning(),),
        )
        payload = hand.to_dict()
        restored = ParsedHand.from_dict(payload)
        self.assertEqual(restored, hand)
        self.assertEqual(restored.to_dict(), payload)
        dumped = json.dumps(payload, sort_keys=True)
        self.assertEqual(dumped, json.dumps(json.loads(dumped), sort_keys=True))
        _assert_payload_safe(payload)

    def test_from_dict_rejects_extra_missing_and_wrong_schema_version(self) -> None:
        payload = _hand().to_dict()
        extra = dict(payload)
        extra["unexpected"] = 1
        with self.assertRaises(VALIDATION_ERRORS):
            ParsedHand.from_dict(extra)
        missing = {key: value for key, value in payload.items() if key != "street_reached"}
        with self.assertRaises(VALIDATION_ERRORS):
            ParsedHand.from_dict(missing)
        wrong_version = dict(payload)
        wrong_version["schema_version"] = 2
        with self.assertRaises(VALIDATION_ERRORS):
            ParsedHand.from_dict(wrong_version)


class RawSourceRefTests(unittest.TestCase):
    """Provenance: source name + SHA-256 hex digest (PLAN-S1 item 7)."""

    def test_source_name_and_sha256_are_validated(self) -> None:
        ref = _source()
        self.assertEqual(ref.source_name, "synthetic-room")
        self.assertEqual(len(ref.source_sha256), 64)
        self.assertEqual(int(ref.source_sha256, 16), int(ref.source_sha256, 16))
        for bad_sha in ("", "a" * 63, "a" * 65, "g" * 64, 123):
            with self.assertRaises(VALIDATION_ERRORS):
                RawSourceRef(source_name="synthetic-room", source_sha256=bad_sha)
        for bad_name in ("", "   ", 42):
            with self.assertRaises(VALIDATION_ERRORS):
                RawSourceRef(source_name=bad_name, source_sha256=_source().source_sha256)


class HandAdapterProtocolTests(unittest.TestCase):
    """``HandAdapter`` protocol (PLAN-S1 item 8)."""

    def test_runtime_checkable_protocol_surface(self) -> None:
        adapter = _SyntheticAdapter()
        # isinstance/issubclass on a Protocol only work when it is
        # runtime_checkable; this assertion is itself the check.
        self.assertTrue(isinstance(adapter, HandAdapter))
        self.assertEqual(adapter.name, "synthetic-fixture")
        self.assertEqual(adapter.CONTRACT_VERSION, CONTRACT_VERSION)

        class _MissingParseAdapter:
            name = "missing-parse"
            CONTRACT_VERSION = CONTRACT_VERSION

        self.assertFalse(isinstance(_MissingParseAdapter(), HandAdapter))


class SyntheticFixtureTests(unittest.TestCase):
    """Synthetic parseable/truncated fixtures (PLAN-S1 item 9)."""

    def test_minimum_and_maximum_seat_cash_hands_parse(self) -> None:
        for raw_text, seat_count in ((_TWO_SEAT_RAW, 2), (_MAX_SEAT_RAW, 9)):
            with self.subTest(seat_count=seat_count):
                result = _SyntheticAdapter().parse(raw_text, "synthetic-room")
                self.assertTrue(result.ok)
                self.assertEqual(len(result.hands), 1)
                self.assertEqual(result.errors, ())
                hand = result.hands[0]
                self.assertEqual(len(hand.table.seats), seat_count)
                self.assertTrue(2 <= hand.button_seat <= 10)
                self.assertEqual(
                    (hand.table.blinds.sb.value, hand.table.blinds.bb.value), (5, 10)
                )
                self.assertEqual(
                    [action.sequence for action in hand.actions], list(range(len(hand.actions)))
                )
                self.assertLessEqual(
                    {action.actor for action in hand.actions},
                    {player.name for player in hand.table.players},
                )
        self.assertEqual(contracts.CONTRACT_VERSION, _SyntheticAdapter.CONTRACT_VERSION)

    def test_truncated_raw_text_yields_errors_and_no_hands(self) -> None:
        result = _SyntheticAdapter().parse(_TRUNCATED_RAW, "synthetic-room")
        self.assertFalse(result.ok)
        self.assertEqual(len(result.hands), 0)  # never synthesized actions
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].code, "truncated-hand")
        self.assertEqual(result.errors[0].kind, "error")


if __name__ == "__main__":
    unittest.main()
