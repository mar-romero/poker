"""Domain model tests for POKER-CORE-MODEL-001.

Covers the canonical immutable domain types (cards, chips, seats, positions,
blind/ante configuration, actions, pots, table metadata) and their
deterministic, versioned round-trip serialization. Validation errors are
asserted as ``(ValueError, TypeError)`` where the exact exception class is an
implementation freedom; the contract is "raise", not "raise one exact class".
"""

from __future__ import annotations

import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal

from poker.domain import cards, game
from poker.domain.cards import DECK, Card, assert_unique_cards
from poker.domain.game import (
    Action,
    ActionKind,
    AmountSemantics,
    BlindAnteConfig,
    Player,
    Position,
    Pot,
    Seat,
    Street,
    TableMetadata,
)
from poker.domain.money import Chips

VALIDATION_ERRORS = (ValueError, TypeError)


def _fold_action() -> Action:
    return Action("hero", Street.PREFLOP, ActionKind.FOLD, None, None, 0, "hh-0001")


def _call_action() -> Action:
    return Action(
        "hero",
        Street.PREFLOP,
        ActionKind.CALL,
        Chips(50),
        AmountSemantics.ADDED,
        0,
        "hh-0001",
    )


def _example_table() -> TableMetadata:
    return TableMetadata(
        (Player("hero", Chips(20000)),),
        (Seat(2),),
        BlindAnteConfig(5, 10, 0),
    )


def _compose(table: TableMetadata, actions: list[Action], pot: Pot) -> dict:
    return {
        "table": table.to_dict(),
        "actions": [action.to_dict() for action in actions],
        "pot": pot.to_dict(),
    }


def _restore(composite: dict) -> tuple[TableMetadata, list[Action], Pot]:
    return (
        TableMetadata.from_dict(composite["table"]),
        [Action.from_dict(node) for node in composite["actions"]],
        Pot.from_dict(composite["pot"]),
    )


def _walk(node: object, visitor) -> None:
    if isinstance(node, dict):
        visitor(node)
        for value in node.values():
            _walk(value, visitor)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _walk(value, visitor)


class CardTests(unittest.TestCase):
    def test_positive_cards_and_ten_symbol(self):
        ace = Card("A", "s")
        self.assertEqual(ace.rank, "A")
        self.assertEqual(ace.suit, "s")
        self.assertEqual(ace.symbol, "As")
        ten = Card("T", "c")
        self.assertEqual(ten.rank, "T")
        self.assertEqual(ten.symbol, "Tc")
        self.assertNotIn("10", cards.RANKS)

    def test_equality_and_hash(self):
        self.assertEqual(Card("A", "s"), Card("A", "s"))
        self.assertNotEqual(Card("A", "s"), Card("A", "h"))
        self.assertNotEqual(Card("A", "s"), Card("2", "s"))
        self.assertEqual(hash(Card("A", "s")), hash(Card("A", "s")))
        self.assertIs({Card("A", "s"): 1}[Card("A", "s")], 1)

    def test_negative_cards(self):
        for rank, suit in (
            ("1", "s"),
            ("X", "h"),
            ("10", "h"),
            ("A", "x"),
            ("A", "S"),
            ("a", "s"),
            ("A", None),
            (None, "s"),
        ):
            with self.assertRaises(VALIDATION_ERRORS):
                Card(rank, suit)

    def test_deck_shape(self):
        # Oracle independence: the expected deck is built from alphabets pinned
        # as LITERALS in this test, not from the production constants, so any
        # alphabet substitution in cards.py is killed by this comparison.
        RANK_ALPHABET = "23456789TJQKA"
        SUIT_ALPHABET = "cdhs"
        self.assertIsInstance(DECK, tuple)
        self.assertEqual(len(DECK), 52)
        assert_unique_cards(DECK)
        expected = tuple(Card(rank, suit) for rank in RANK_ALPHABET for suit in SUIT_ALPHABET)
        self.assertEqual(DECK, expected)
        self.assertEqual(
            {card.symbol for card in DECK},
            {rank + suit for rank in RANK_ALPHABET for suit in SUIT_ALPHABET},
        )
        # The production alphabets must match the pinned literals exactly
        # (same members, same length), and ten must stay 'T', never '10'.
        self.assertEqual(set(cards.RANKS), set(RANK_ALPHABET))
        self.assertEqual(len(cards.RANKS), len(RANK_ALPHABET))
        self.assertEqual(set(cards.SUITS), set(SUIT_ALPHABET))
        self.assertEqual(len(cards.SUITS), len(SUIT_ALPHABET))
        self.assertNotIn("10", cards.RANKS)

    def test_deck_read_twice_is_deterministic(self):
        self.assertEqual(tuple(DECK), tuple(cards.DECK))
        self.assertTrue(all(isinstance(card, Card) for card in DECK))

    def test_assert_unique_cards(self):
        assert_unique_cards([])
        assert_unique_cards([Card("A", "s"), Card("2", "h")])
        with self.assertRaises(ValueError):
            assert_unique_cards([Card("A", "s"), Card("A", "s")])

    def test_card_serialization_round_trip(self):
        card = Card("A", "s")
        payload = card.to_dict()
        self.assertEqual(payload["schema_version"], game.SCHEMA_VERSION)
        self.assertIs(type(payload["schema_version"]), int)
        self.assertEqual(Card.from_dict(payload), card)
        self.assertEqual(Card.from_dict(payload).to_dict(), payload)
        with self.assertRaises(VALIDATION_ERRORS):
            Card.from_dict({**payload, "schema_version": game.SCHEMA_VERSION + 1})
        with self.assertRaises(VALIDATION_ERRORS):
            Card.from_dict({"rank": "A", "suit": "s"})
        with self.assertRaises(VALIDATION_ERRORS):
            Card.from_dict({**payload, "rank": "1"})
        with self.assertRaises(VALIDATION_ERRORS):
            Card.from_dict({**payload, "extra": 1})


class ChipsTests(unittest.TestCase):
    def test_zero_and_huge_values(self):
        self.assertIs(type(Chips(0).value), int)
        self.assertEqual(Chips(0).value, 0)
        big = Chips(2**63)
        self.assertEqual(big.value, 2**63)
        self.assertIs(type(big.value), int)

    def test_equality_hash_dict_key_and_set_dedup(self):
        self.assertEqual(Chips(5), Chips(5))
        self.assertNotEqual(Chips(5), Chips(6))
        self.assertEqual(hash(Chips(5)), hash(Chips(5)))
        self.assertEqual({Chips(5): "a"}[Chips(5)], "a")
        self.assertEqual(len({Chips(5), Chips(5), Chips(6)}), 2)

    def test_rejects_non_int_and_negative(self):
        for bad in (-1, True, False, 1.5, 2.0, "100", Decimal("100"), None, [1]):
            with self.assertRaises(VALIDATION_ERRORS):
                Chips(bad)

    def test_value_is_plain_int_not_bool(self):
        chips = Chips(7)
        self.assertIs(type(chips.value), int)
        self.assertNotIsInstance(chips.value, bool)

    def test_frozen(self):
        chips = Chips(1)
        with self.assertRaises(FrozenInstanceError):
            chips.value = 2

    def test_amount_node_serialization(self):
        payload = Chips(50).to_dict()
        self.assertEqual(payload["value"], 50)
        self.assertEqual(payload["unit"], "chips")
        self.assertIs(type(payload["value"]), int)
        self.assertNotIsInstance(payload["value"], bool)
        self.assertEqual(payload["schema_version"], game.SCHEMA_VERSION)
        self.assertEqual(Chips.from_dict(payload), Chips(50))
        self.assertEqual(Chips.from_dict(payload).to_dict(), payload)

    def test_from_dict_rejects_bad_amount_nodes(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict({"value": 1.5, "unit": "chips", "schema_version": game.SCHEMA_VERSION})
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict({"value": True, "unit": "chips", "schema_version": game.SCHEMA_VERSION})
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict({"value": -3, "unit": "chips", "schema_version": game.SCHEMA_VERSION})
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict({"value": 5, "unit": "dollars", "schema_version": game.SCHEMA_VERSION})
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict({"value": 5})
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict(
                {"value": 5, "unit": "chips", "schema_version": game.SCHEMA_VERSION, "extra": 1}
            )


class StreetAndPositionTests(unittest.TestCase):
    def test_street_ordering(self):
        self.assertLess(Street.PREFLOP, Street.FLOP)
        self.assertLess(Street.FLOP, Street.TURN)
        self.assertLess(Street.TURN, Street.RIVER)
        self.assertEqual(
            [street.name for street in Street],
            ["PREFLOP", "FLOP", "TURN", "RIVER"],
        )

    def test_positions_include_blinds_and_button(self):
        for name in ("SB", "BB", "BTN"):
            self.assertIsNotNone(Position[name])


class SeatTests(unittest.TestCase):
    def test_valid_range(self):
        self.assertEqual(Seat(2).number, 2)
        self.assertEqual(Seat(10).number, 10)

    def test_rejects(self):
        for bad in (1, 11, -1, 0, 2.0, True, None, "3"):
            with self.assertRaises(VALIDATION_ERRORS):
                Seat(bad)

    def test_frozen(self):
        seat = Seat(2)
        with self.assertRaises(FrozenInstanceError):
            seat.number = 3


class BlindAnteConfigTests(unittest.TestCase):
    def test_sb_gt_bb_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            BlindAnteConfig(10, 5, 0)

    def test_sb_equals_bb_allowed(self):
        config = BlindAnteConfig(5, 5, 0)
        self.assertEqual((config.sb, config.bb, config.ante), (Chips(5), Chips(5), Chips(0)))

    def test_negative_ante_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            BlindAnteConfig(5, 10, -1)

    def test_normal_config(self):
        config = BlindAnteConfig(5, 10, 0)
        self.assertEqual((config.sb, config.bb, config.ante), (Chips(5), Chips(10), Chips(0)))
        chips_config = BlindAnteConfig(Chips(5), Chips(10), Chips(1))
        self.assertEqual(chips_config.ante, Chips(1))

    def test_wheel_config_allowed(self):
        config = BlindAnteConfig(0, 0, 0)
        self.assertEqual((config.sb, config.bb, config.ante), (Chips(0), Chips(0), Chips(0)))

    def test_frozen(self):
        config = BlindAnteConfig(5, 10, 0)
        with self.assertRaises(FrozenInstanceError):
            config.sb = Chips(1)


class ActionTests(unittest.TestCase):
    def test_call_constructs(self):
        action = _call_action()
        self.assertEqual(action.actor, "hero")
        self.assertEqual(action.street, Street.PREFLOP)
        self.assertEqual(action.kind, ActionKind.CALL)
        self.assertEqual(action.amount, Chips(50))
        self.assertEqual(action.amount_semantics, AmountSemantics.ADDED)
        self.assertEqual(action.sequence, 0)
        self.assertEqual(action.provenance, "hh-0001")

    def test_bet_constructs(self):
        action = Action(
            "villain",
            Street.FLOP,
            ActionKind.BET,
            Chips(120),
            AmountSemantics.TOTAL_TO,
            3,
            "hh-0002",
        )
        self.assertEqual(action.kind, ActionKind.BET)
        self.assertEqual(action.amount, Chips(120))
        self.assertEqual(action.amount_semantics, AmountSemantics.TOTAL_TO)

    def test_fold_without_amount_constructs(self):
        action = _fold_action()
        self.assertIsNone(action.amount)
        self.assertIsNone(action.amount_semantics)
        self.assertEqual(action.kind, ActionKind.FOLD)

    def test_fold_with_amount_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.FOLD,
                Chips(10),
                AmountSemantics.ADDED,
                0,
                "hh-0001",
            )

    def test_check_with_amount_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.CHECK,
                Chips(0),
                None,
                0,
                "hh-0001",
            )

    def test_amount_carrying_kind_requires_amount(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.CALL,
                None,
                None,
                0,
                "hh-0001",
            )

    def test_amount_carrying_kind_requires_semantics(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.BET,
                Chips(20),
                None,
                0,
                "hh-0001",
            )

    def test_provenance_required(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.CALL,
                Chips(50),
                AmountSemantics.ADDED,
                0,
                None,
            )
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.CALL,
                Chips(50),
                AmountSemantics.ADDED,
                0,
                "",
            )

    def test_invalid_amount_semantics_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.CALL,
                Chips(50),
                "added",
                0,
                "hh-0001",
            )

    def test_negative_sequence_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.CALL,
                Chips(50),
                AmountSemantics.ADDED,
                -1,
                "hh-0001",
            )

    def test_bool_sequence_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Action(
                "hero",
                Street.PREFLOP,
                ActionKind.CALL,
                Chips(50),
                AmountSemantics.ADDED,
                True,
                "hh-0001",
            )

    def test_frozen(self):
        action = _call_action()
        with self.assertRaises(FrozenInstanceError):
            action.amount = Chips(1)


class PotTests(unittest.TestCase):
    def test_total_includes_rake(self):
        pot = Pot({"hero": Chips(100), "villain": Chips(60)}, Chips(5))
        self.assertEqual(pot.total, Chips(165))
        self.assertEqual(
            pot.total.value - pot.rake.value,
            sum(value.value for value in pot.contributions.values()),
        )

    def test_default_rake_is_zero(self):
        pot = Pot({"hero": Chips(10)})
        self.assertEqual(pot.rake, Chips(0))
        self.assertEqual(pot.total, Chips(10))

    def test_rejects_bad_contributions(self):
        with self.assertRaises(VALIDATION_ERRORS):
            Pot({"hero": 10})
        with self.assertRaises(VALIDATION_ERRORS):
            Pot({"": Chips(10)})
        with self.assertRaises(VALIDATION_ERRORS):
            Pot({"hero": Chips(-5)})
        with self.assertRaises(VALIDATION_ERRORS):
            Pot("hero")

    def test_contributions_mapping_is_read_only(self):
        pot = Pot({"hero": Chips(10)})
        with self.assertRaises(TypeError):
            pot.contributions["hero"] = Chips(5)

    def test_frozen(self):
        pot = Pot({"hero": Chips(10)})
        with self.assertRaises(FrozenInstanceError):
            pot.rake = Chips(1)

    def test_serialization_round_trip_and_rake_key(self):
        pot = Pot({"hero": Chips(100), "villain": Chips(60)}, Chips(5))
        payload = pot.to_dict()
        self.assertEqual(sorted(payload), ["contributions", "rake", "schema_version"])
        self.assertEqual(payload["rake"], {"value": 5, "unit": "chips", "schema_version": game.SCHEMA_VERSION})
        self.assertEqual(payload["contributions"]["hero"], {"value": 100, "unit": "chips", "schema_version": game.SCHEMA_VERSION})
        self.assertEqual(Pot.from_dict(payload), pot)
        self.assertEqual(Pot.from_dict(payload).to_dict(), payload)
        with self.assertRaises(VALIDATION_ERRORS):
            Pot.from_dict({**payload, "schema_version": game.SCHEMA_VERSION + 1})
        with self.assertRaises(VALIDATION_ERRORS):
            Pot.from_dict({"contributions": {}, "rake": {"value": 0, "unit": "chips"}})


class TableMetadataTests(unittest.TestCase):
    def _table(self, names, seat_numbers, blinds=(5, 10, 0)):
        return TableMetadata(
            tuple(Player(name, Chips(1000)) for name in names),
            tuple(Seat(number) for number in seat_numbers),
            BlindAnteConfig(*blinds),
        )

    def test_valid_table(self):
        table = self._table(["hero", "villain"], (2, 3))
        self.assertEqual(len(table.players), 2)
        self.assertEqual(len(table.seats), 2)

    def test_duplicate_seat_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            self._table(["hero", "villain"], (3, 3))

    def test_mismatched_players_and_seats_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            self._table(["hero", "villain"], (2, 3, 4))

    def test_duplicate_player_names_rejected(self):
        with self.assertRaises(VALIDATION_ERRORS):
            self._table(["hero", "hero"], (2, 3))

    def test_frozen(self):
        table = self._table(["hero"], (2,))
        with self.assertRaises(FrozenInstanceError):
            table.players = ()


class RoundTripTests(unittest.TestCase):
    def _heads_up(self) -> tuple[TableMetadata, list[Action], Pot]:
        table = TableMetadata(
            (Player("hero", Chips(20000)), Player("villain", Chips(20000))),
            (Seat(2), Seat(3)),
            BlindAnteConfig(5, 10, 0),
        )
        actions = [
            Action("hero", Street.PREFLOP, ActionKind.POST, Chips(5), AmountSemantics.ADDED, 0, "hh-0001"),
            Action("villain", Street.PREFLOP, ActionKind.POST, Chips(10), AmountSemantics.ADDED, 1, "hh-0001"),
            Action("hero", Street.PREFLOP, ActionKind.CALL, Chips(10), AmountSemantics.ADDED, 2, "hh-0001"),
            Action("villain", Street.PREFLOP, ActionKind.CHECK, None, None, 3, "hh-0001"),
            Action("villain", Street.FLOP, ActionKind.BET, Chips(25), AmountSemantics.TOTAL_TO, 4, "hh-0001"),
            Action("hero", Street.FLOP, ActionKind.CALL, Chips(25), AmountSemantics.ADDED, 5, "hh-0001"),
        ]
        pot = Pot({"hero": Chips(35), "villain": Chips(35)}, Chips(1))
        return table, actions, pot

    def _six_max(self) -> tuple[TableMetadata, list[Action], Pot]:
        names = ["utg", "mp", "co", "btn", "sb", "bb"]
        table = TableMetadata(
            tuple(Player(name, Chips(10000)) for name in names),
            tuple(Seat(number) for number in range(2, 8)),
            BlindAnteConfig(10, 20, 2),
        )
        actions = [
            Action("sb", Street.PREFLOP, ActionKind.POST, Chips(10), AmountSemantics.ADDED, 0, "hh-0002"),
            Action("bb", Street.PREFLOP, ActionKind.POST, Chips(20), AmountSemantics.ADDED, 1, "hh-0002"),
            Action("utg", Street.PREFLOP, ActionKind.RAISE, Chips(60), AmountSemantics.TOTAL_TO, 2, "hh-0002"),
            Action("co", Street.PREFLOP, ActionKind.CALL, Chips(60), AmountSemantics.ADDED, 3, "hh-0002"),
            Action("mp", Street.PREFLOP, ActionKind.FOLD, None, None, 4, "hh-0002"),
            Action("btn", Street.PREFLOP, ActionKind.FOLD, None, None, 5, "hh-0002"),
            Action("sb", Street.PREFLOP, ActionKind.FOLD, None, None, 6, "hh-0002"),
            Action("bb", Street.PREFLOP, ActionKind.CALL, Chips(40), AmountSemantics.ADDED, 7, "hh-0002"),
            Action("bb", Street.FLOP, ActionKind.CHECK, None, None, 8, "hh-0002"),
            Action("utg", Street.FLOP, ActionKind.BET, Chips(80), AmountSemantics.TOTAL_TO, 9, "hh-0002"),
            Action("co", Street.FLOP, ActionKind.CALL, Chips(80), AmountSemantics.ADDED, 10, "hh-0002"),
            Action("bb", Street.FLOP, ActionKind.FOLD, None, None, 11, "hh-0002"),
            Action("utg", Street.TURN, ActionKind.BET, Chips(160), AmountSemantics.TOTAL_TO, 12, "hh-0002"),
            Action("co", Street.TURN, ActionKind.RAISE, Chips(320), AmountSemantics.TOTAL_TO, 13, "hh-0002"),
            Action("utg", Street.TURN, ActionKind.FOLD, None, None, 14, "hh-0002"),
        ]
        pot = Pot({name: Chips(200) for name in names}, Chips(3))
        return table, actions, pot

    def _assert_round_trip(self, table, actions, pot):
        composite = _compose(table, actions, pot)
        restored = _restore(composite)
        self.assertEqual(restored[0], table)
        self.assertEqual(restored[1], actions)
        self.assertEqual(restored[2], pot)
        restored_pot = restored[2]
        self.assertEqual(
            sum(value.value for value in restored_pot.contributions.values())
            + restored_pot.rake.value,
            restored_pot.total.value,
        )
        self.assertEqual(
            sum(value.value for value in restored_pot.contributions.values()),
            restored_pot.total.value - restored_pot.rake.value,
        )
        recomposed = _compose(*restored)
        self.assertEqual(composite, recomposed)
        dumps = [json.dumps(composite, sort_keys=True)]
        current = composite
        for _ in range(2):
            current = _compose(*_restore(current))
            dumps.append(json.dumps(current, sort_keys=True))
        self.assertEqual(dumps[0], dumps[1])
        self.assertEqual(dumps[1], dumps[2])
        return composite

    def test_heads_up_round_trip(self):
        table, actions, pot = self._heads_up()
        self._assert_round_trip(table, actions, pot)

    def test_six_max_round_trip(self):
        table, actions, pot = self._six_max()
        composite = self._assert_round_trip(table, actions, pot)
        self.assertGreaterEqual(len({action.street for action in actions}), 2)
        self.assertIn("bb", composite["pot"]["contributions"])


class SerializationStabilityTests(unittest.TestCase):
    def _composites(self):
        for builder in (RoundTripTests._heads_up, RoundTripTests._six_max):
            table, actions, pot = builder(self)
            yield _compose(table, actions, pot)

    def test_no_float_or_set_nodes(self):
        for composite in self._composites():
            bad = []

            def visit(node):
                if isinstance(node, float):
                    bad.append(("float", node))
                if isinstance(node, (set, frozenset)):
                    bad.append(("set", node))
                if isinstance(node, dict) and "unit" in node:
                    if set(node) != {"schema_version", "value", "unit"}:
                        bad.append(("keys", sorted(node)))
                    if node["unit"] != "chips":
                        bad.append(("unit", node["unit"]))
                    if type(node["value"]) is not int or isinstance(node["value"], bool):
                        bad.append(("value", node["value"]))

            _walk(composite, visit)
            self.assertEqual(bad, [])

    def test_version_pin_and_fail_closed(self):
        table, actions, pot = RoundTripTests._heads_up(self)
        composite = _compose(table, actions, pot)
        for section in ("table", "actions", "pot"):
            node = composite[section]
            node = node[0] if isinstance(node, list) else node
            self.assertEqual(node["schema_version"], game.SCHEMA_VERSION)
            self.assertIs(type(node["schema_version"]), int)
        for section, from_dict in (
            ("table", TableMetadata.from_dict),
            ("pot", Pot.from_dict),
        ):
            payload = composite[section]
            with self.assertRaises(VALIDATION_ERRORS):
                from_dict({**payload, "schema_version": game.SCHEMA_VERSION + 1})
            with self.assertRaises(VALIDATION_ERRORS):
                missing = copy.deepcopy(payload)
                del missing["schema_version"]
                from_dict(missing)
        action_payload = composite["actions"][0]
        with self.assertRaises(VALIDATION_ERRORS):
            Action.from_dict({**action_payload, "schema_version": game.SCHEMA_VERSION + 1})
        with self.assertRaises(VALIDATION_ERRORS):
            missing = copy.deepcopy(action_payload)
            del missing["schema_version"]
            Action.from_dict(missing)

    def test_from_dict_rejects_float_stacks(self):
        table, _actions, _pot = RoundTripTests._heads_up(self)
        payload = table.to_dict()
        payload = copy.deepcopy(payload)
        payload["players"][0]["stack"]["value"] = 100.5
        with self.assertRaises(VALIDATION_ERRORS):
            TableMetadata.from_dict(payload)

    def test_schema_version_constant_is_int(self):
        self.assertIs(type(game.SCHEMA_VERSION), int)
        self.assertGreaterEqual(game.SCHEMA_VERSION, 1)


class ImmutabilityTests(unittest.TestCase):
    def _cases(self):
        return [
            Card("A", "s"),
            Chips(1),
            Seat(2),
            Player("hero", Chips(1)),
            _call_action(),
            _fold_action(),
            BlindAnteConfig(5, 10, 0),
            Pot({"hero": Chips(1)}),
            _example_table(),
        ]

    def test_setattr_raises_frozen_instance_error(self):
        for obj in self._cases():
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, "nonexistent_attr", None)


class HardeningTests(unittest.TestCase):
    """Audit-gap pins (POKER-CORE-MODEL-001 hardening round).

    These tests exist to freeze behaviors the production code already enforces
    (nested schema-version checks, exact-key payload validation, strict enum
    tokens, Chips typing and a literal deck alphabet) so future refactors
    cannot silently drop them. Production files are not modified here.
    """

    def _player_payload(self) -> dict:
        return Player("hero", Chips(20000)).to_dict()

    def _seat_payload(self) -> dict:
        return Seat(2).to_dict()

    def _blinds_payload(self) -> dict:
        return BlindAnteConfig(5, 10, 0).to_dict()

    def _action_payload(self) -> dict:
        return _call_action().to_dict()

    def _table_payload(self) -> dict:
        return _example_table().to_dict()

    @staticmethod
    def _drop(payload: dict, key: str) -> dict:
        return {name: value for name, value in payload.items() if name != key}

    @staticmethod
    def _bump_version(payload: dict) -> dict:
        return {**payload, "schema_version": game.SCHEMA_VERSION + 1}

    def test_nested_version_pin_missing_and_wrong(self):
        cases = (
            ("Player", Player.from_dict, self._player_payload()),
            ("Seat", Seat.from_dict, self._seat_payload()),
            ("BlindAnteConfig", BlindAnteConfig.from_dict, self._blinds_payload()),
        )
        for label, from_dict, payload in cases:
            with self.subTest(label=label, mutation="missing"):
                with self.assertRaises(VALIDATION_ERRORS):
                    from_dict(self._drop(payload, "schema_version"))
            with self.subTest(label=label, mutation="wrong_int"):
                with self.assertRaises(VALIDATION_ERRORS):
                    from_dict(self._bump_version(payload))
            with self.subTest(label=label, mutation="string_version"):
                with self.assertRaises(VALIDATION_ERRORS):
                    from_dict({**payload, "schema_version": "1"})

    def test_exact_keys_extra_key_rejected(self):
        cases = (
            ("Player", Player.from_dict, self._player_payload()),
            ("Seat", Seat.from_dict, self._seat_payload()),
            ("BlindAnteConfig", BlindAnteConfig.from_dict, self._blinds_payload()),
            ("Action", Action.from_dict, self._action_payload()),
            ("TableMetadata", TableMetadata.from_dict, self._table_payload()),
        )
        for label, from_dict, payload in cases:
            with self.subTest(label=label):
                with self.assertRaises(VALIDATION_ERRORS):
                    from_dict({**payload, "extra_unknown_key": 1})

    def test_exact_keys_missing_required_key_rejected(self):
        cases = (
            ("Player", Player.from_dict, self._player_payload(), "name"),
            ("Seat", Seat.from_dict, self._seat_payload(), "number"),
            ("BlindAnteConfig", BlindAnteConfig.from_dict, self._blinds_payload(), "ante"),
            ("Action", Action.from_dict, self._action_payload(), "provenance"),
            ("TableMetadata", TableMetadata.from_dict, self._table_payload(), "blinds"),
        )
        for label, from_dict, payload, key in cases:
            with self.subTest(label=label, key=key):
                with self.assertRaises(VALIDATION_ERRORS):
                    from_dict(self._drop(payload, key))

    def test_action_actor_negatives(self):
        for actor in ("", "   ", None):
            with self.subTest(actor=actor):
                with self.assertRaises(VALIDATION_ERRORS):
                    Action(actor, Street.PREFLOP, ActionKind.FOLD, None, None, 0, "hh-0001")

    def test_action_from_dict_unknown_enum_tokens_raise_value_error(self):
        payload = self._action_payload()
        for key, token in (
            ("kind", "FOLDED"),
            ("street", "FLOPPY"),
            ("amount_semantics", "ADDED_TO"),
        ):
            with self.subTest(key=key, token=token):
                # The exact contract: a ValueError (enum token converted by
                # _enum_from_name), never an unconverted KeyError.
                try:
                    Action.from_dict({**payload, key: token})
                except KeyError as exc:  # pragma: no cover - regression guard
                    self.fail(f"unknown {key} token raised KeyError instead of ValueError: {exc!r}")
                except ValueError:
                    pass
                else:  # pragma: no cover - regression guard
                    self.fail(f"unknown {key} token was silently accepted")

    def test_pot_rake_non_chips_rejected(self):
        for rake in (5, 5.0, "5", None, True):
            with self.subTest(rake=rake):
                with self.assertRaises(VALIDATION_ERRORS):
                    Pot({"p": Chips(10)}, rake=rake)


if __name__ == "__main__":
    unittest.main()
