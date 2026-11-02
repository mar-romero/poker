"""Normalized hand-history ingest contract.

This module is the provider-neutral boundary between venue-specific raw-text
parsers (implemented elsewhere) and the canonical domain model. It defines:

- ``RawSourceRef``: provenance of the raw text a parse was run against.
- ``ParseIssue``: one contract-level warning or error.
- ``ParsedHand``: one structurally valid normalized hand, directly feedable to
  ``StateEngine.replay`` (button seat, ordered ``Action`` tuple, blinds via
  ``TableMetadata``).
- ``ParseResult``: the outcome of one ``HandAdapter.parse`` call with the
  pinned validity matrix below.
- ``HandAdapter``: the ``runtime_checkable`` parser protocol.
- ``CONTRACT_VERSION``: parser/contract version recorded as
  ``hands.parser_version`` by the ingest pipeline.

Import surface: this module imports ONLY from ``poker.domain.game`` (the
canonical domain source of truth) and ``poker.domain.money``. It never imports
from ``poker.domain.pots`` (a parallel definition set) or ``poker.engine.state``
(engine concerns stay behind the replay boundary).

Serialization rules (mirror ``poker.domain.game``):

- Payloads are plain dicts/lists of ``str`` / ``int`` / ``None`` — never floats
  and never sets — so ``json.dumps(payload, sort_keys=True)`` is byte-stable
  across ``to_dict``/``from_dict`` cycles.
- The payload embeds a single contract ``schema_version`` (the domain
  ``SCHEMA_VERSION`` integer, currently 1), validated fail-closed by
  ``require_schema_version`` plus exact-key ``require_exact_keys`` checks at
  the top level and for every nested node.
- Two-layer dtype rule: chip amounts live as ``Chips`` (or ``BlindAnteConfig``)
  in runtime domain objects, but serialized monetary values are plain
  non-negative ints, matching the DB ``Integer`` columns on ``hands``
  (``small_blind``/``big_blind``/``ante``) and ``hand_players.stack``. Floats,
  bools, strings and ``Decimal`` are rejected at the boundary.
- Structural validation only: this module deliberately contains no
  legal-action engine. Full legality is ``StateEngine.replay``'s job; a
  ``ParsedHand`` guarantees only the structural preconditions replay checks
  first (types, ordering, bounds).

Validity matrix for ``ParseResult`` (pinned):

===============  ==================  ==================  ============
hands            warnings            errors              meaning
===============  ==================  ==================  ============
non-empty        empty               empty               clean success
non-empty        non-empty           empty               partial success
empty            empty               empty               no hands found (valid)
empty            any                 non-empty           invalid input
non-empty        any                 non-empty           FORBIDDEN (raises)
===============  ==================  ==================  ============

``ParseResult.ok`` is ``True`` exactly when ``errors`` is empty; a valid
result may still contain zero hands (``no hands found``), so callers must
check ``hands`` separately.

Fabrication rule: a parser NEVER synthesizes actions, cards or hands that the
raw text does not contain. Unparseable or truncated input yields ``errors``
with zero hands; gaps are surfaced as ``warnings``, never filled silently.

``CONTRACT_VERSION`` semver discipline:

- MAJOR: a change that invalidates previously parsed normalized hands (shape
  or semantics break; old rows must not be mixed with new ones).
- MINOR: additive, backward-compatible contract capability (e.g. the future
  tournament metadata slot); previously parsed hands remain valid.
- PATCH: fixes with no observable payload-shape change.

A version bump is INTENTIONAL dedup divergence, not a bug: the ``hands``
table dedups on ``(raw_source_id, table_name, hand_number, parser_version)``
and audits re-parses on ``(content_sha256, parser_version)``, so bumping
``CONTRACT_VERSION`` deliberately creates new rows for the same raw content
instead of silently overwriting previously parsed hands.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol, runtime_checkable

from poker.domain.game import (
    SCHEMA_VERSION,
    Action,
    ActionKind,
    AmountSemantics,
    BlindAnteConfig,
    Player,
    Seat,
    Street,
    TableMetadata,
    require_exact_keys,
    require_schema_version,
)
from poker.domain.money import Chips

__all__ = [
    "CONTRACT_VERSION",
    "HandAdapter",
    "ParseIssue",
    "ParsedHand",
    "ParseResult",
    "RawSourceRef",
]

CONTRACT_VERSION = "1.0.0"

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_VALID_ISSUE_KINDS = frozenset({"warning", "error"})
_MIN_SEATS = 2
_MAX_SEATS = 10
_MIN_BUTTON_SEAT = 2
_MAX_BUTTON_SEAT = 10
_MAX_STREET = 3  # Street.RIVER

_RAW_SOURCE_REF_KEYS = frozenset({"schema_version", "source_name", "source_sha256"})
_PARSED_HAND_KEYS = frozenset(
    {
        "schema_version",
        "table_name",
        "hand_number",
        "button_seat",
        "street_reached",
        "blinds",
        "players",
        "actions",
        "warnings",
    }
)
_BLINDS_KEYS = frozenset({"sb", "bb", "ante"})
_PLAYER_KEYS = frozenset({"name", "seat", "stack"})
_ACTION_KEYS = frozenset(
    {"actor", "street", "kind", "amount", "amount_semantics", "sequence", "provenance"}
)
_PARSE_ISSUE_KEYS = frozenset({"schema_version", "kind", "code", "message", "position"})


def _require_plain_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be a plain int, got {type(value).__name__}")
    return value


def _require_non_empty_str(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


@dataclasses.dataclass(frozen=True)
class RawSourceRef:
    """Provenance of the raw text one parse was run against.

    Carries the external source identifier (e.g. room/site name) and the
    SHA-256 hex digest of the exact raw content, so every normalized hand can
    be traced to its source bytes (``hands.content_sha256`` audit column).
    """

    source_name: str
    source_sha256: str

    def __post_init__(self) -> None:
        _require_non_empty_str(self.source_name, "RawSourceRef.source_name")
        if type(self.source_sha256) is not str:
            raise TypeError(
                f"RawSourceRef.source_sha256 must be a str, got {type(self.source_sha256).__name__}"
            )
        if len(self.source_sha256) != 64 or not set(self.source_sha256) <= _HEX_DIGITS:
            raise ValueError(
                "RawSourceRef.source_sha256 must be a 64-character hex digest, got "
                f"{self.source_sha256!r}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "RawSourceRef":
        require_schema_version(payload)
        require_exact_keys(payload, _RAW_SOURCE_REF_KEYS, "RawSourceRef")
        return cls(payload["source_name"], payload["source_sha256"])


@dataclasses.dataclass(frozen=True)
class ParseIssue:
    """One contract-level diagnostic, discriminated by ``kind``.

    ``kind`` is exactly ``'warning'`` (tolerable deviation recorded, parsing
    continued) or ``'error'`` (input could not be trusted, no hand produced).
    Warnings and errors are separate lists everywhere and are NEVER merged.
    """

    kind: str
    code: str
    message: str
    position: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in _VALID_ISSUE_KINDS:
            raise ValueError(
                f"ParseIssue.kind must be 'warning' or 'error', got {self.kind!r}"
            )
        _require_non_empty_str(self.code, "ParseIssue.code")
        _require_non_empty_str(self.message, "ParseIssue.message")
        if self.position is not None:
            _require_plain_int(self.position, "ParseIssue.position")
            if self.position < 0:
                raise ValueError(f"ParseIssue.position must be non-negative, got {self.position}")

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ParseIssue":
        require_schema_version(payload)
        require_exact_keys(payload, _PARSE_ISSUE_KEYS, "ParseIssue")
        return cls(
            payload["kind"], payload["code"], payload["message"], payload["position"]
        )


@dataclasses.dataclass(frozen=True)
class ParsedHand:
    """One structurally valid normalized hand.

    Directly feedable to ``StateEngine.replay(table, table.blinds, actions,
    button_seat=button_seat)``: ``table`` supplies players/seats/blinds, the
    engine's REQUIRED ``button_seat`` kwarg is carried here, and ``actions``
    are canonical ``Action`` instances already in replay order (the engine
    replays in list order and never re-sorts). Structural validation only:
    legal-action checks remain the engine's job.
    """

    table: TableMetadata
    actions: tuple[Action, ...]
    button_seat: int
    hand_number: str
    table_name: str
    street_reached: int
    warnings: tuple[ParseIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.table, TableMetadata):
            raise TypeError(
                f"ParsedHand.table must be a TableMetadata, got {type(self.table).__name__}"
            )
        seat_count = len(self.table.seats)
        if not _MIN_SEATS <= seat_count <= _MAX_SEATS:
            raise ValueError(
                f"ParsedHand requires {_MIN_SEATS}..{_MAX_SEATS} seats, got {seat_count}"
            )
        _require_plain_int(self.button_seat, "ParsedHand.button_seat")
        if not _MIN_BUTTON_SEAT <= self.button_seat <= _MAX_BUTTON_SEAT:
            raise ValueError(
                f"ParsedHand.button_seat must be between {_MIN_BUTTON_SEAT} and "
                f"{_MAX_BUTTON_SEAT}, got {self.button_seat}"
            )
        if self.button_seat not in {seat.number for seat in self.table.seats}:
            raise ValueError(
                f"ParsedHand.button_seat {self.button_seat} is not an occupied seat "
                f"(hands.button_seat references hand_players)"
            )
        _require_non_empty_str(self.hand_number, "ParsedHand.hand_number")
        _require_non_empty_str(self.table_name, "ParsedHand.table_name")
        _require_plain_int(self.street_reached, "ParsedHand.street_reached")
        if not 0 <= self.street_reached <= _MAX_STREET:
            raise ValueError(
                f"ParsedHand.street_reached must be between 0 and {_MAX_STREET}, "
                f"got {self.street_reached}"
            )
        actions = tuple(self.actions)
        if not all(isinstance(action, Action) for action in actions):
            raise TypeError("ParsedHand.actions must contain canonical Action instances")
        sequences = [action.sequence for action in actions]
        if any(later <= earlier for earlier, later in zip(sequences, sequences[1:])):
            raise ValueError(
                "ParsedHand.actions must have strictly increasing sequences with no "
                f"duplicates, got {sequences}"
            )
        warnings = tuple(self.warnings)
        if not all(
            isinstance(issue, ParseIssue) and issue.kind == "warning" for issue in warnings
        ):
            raise ValueError(
                "ParsedHand.warnings must contain 'warning'-kind ParseIssue instances"
            )
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "warnings", warnings)

    def to_dict(self) -> dict:
        blinds = self.table.blinds
        players_by_name = {player.name: player for player in self.table.players}
        seat_by_name = {
            player.name: seat.number
            for player, seat in zip(self.table.players, self.table.seats)
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "table_name": self.table_name,
            "hand_number": self.hand_number,
            "button_seat": self.button_seat,
            "street_reached": self.street_reached,
            # Monetary values are plain non-negative ints (DB Integer layer).
            "blinds": {
                "sb": blinds.sb.value,
                "bb": blinds.bb.value,
                "ante": blinds.ante.value,
            },
            "players": [
                {
                    "name": player.name,
                    "seat": seat_by_name[player.name],
                    "stack": players_by_name[player.name].stack.value,
                }
                for player in self.table.players
            ],
            "actions": [
                {
                    "actor": action.actor,
                    "street": action.street.name,
                    "kind": action.kind.name,
                    "amount": None if action.amount is None else action.amount.value,
                    "amount_semantics": (
                        None
                        if action.amount_semantics is None
                        else action.amount_semantics.name
                    ),
                    "sequence": action.sequence,
                    "provenance": action.provenance,
                }
                for action in self.actions
            ],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ParsedHand":
        require_schema_version(payload)
        require_exact_keys(payload, _PARSED_HAND_KEYS, "ParsedHand")
        blinds_payload = payload["blinds"]
        require_exact_keys(blinds_payload, _BLINDS_KEYS, "ParsedHand.blinds")
        if not isinstance(payload["players"], list) or not isinstance(
            payload["actions"], list
        ):
            raise TypeError("ParsedHand payload 'players'/'actions' must be lists")
        players: list[Player] = []
        seats: list[Seat] = []
        for node in payload["players"]:
            require_exact_keys(node, _PLAYER_KEYS, "ParsedHand.player")
            players.append(Player(node["name"], Chips(node["stack"])))
            seats.append(Seat(node["seat"]))
        actions: list[Action] = []
        for node in payload["actions"]:
            require_exact_keys(node, _ACTION_KEYS, "ParsedHand.action")
            actions.append(
                Action(
                    node["actor"],
                    Street[node["street"]],
                    ActionKind[node["kind"]],
                    None if node["amount"] is None else Chips(node["amount"]),
                    (
                        None
                        if node["amount_semantics"] is None
                        else AmountSemantics[node["amount_semantics"]]
                    ),
                    node["sequence"],
                    node["provenance"],
                )
            )
        warnings = tuple(
            ParseIssue.from_dict(node)
            for node in payload["warnings"]
        )
        return cls(
            table=TableMetadata(
                tuple(players),
                tuple(seats),
                BlindAnteConfig(
                    Chips(blinds_payload["sb"]),
                    Chips(blinds_payload["bb"]),
                    Chips(blinds_payload["ante"]),
                ),
            ),
            actions=tuple(actions),
            button_seat=payload["button_seat"],
            hand_number=payload["hand_number"],
            table_name=payload["table_name"],
            street_reached=payload["street_reached"],
            warnings=warnings,
        )


@dataclasses.dataclass(frozen=True)
class ParseResult:
    """Outcome of one ``HandAdapter.parse`` call.

    Pinned validity matrix (see module docstring): ``ok`` is ``True`` exactly
    when ``errors`` is empty; ``errors`` non-empty FORBIDS hands (a result is
    either trustable-with-noted-gaps or untrustable, never both); an empty
    hands/errors result is a valid "no hands found" outcome. ``warnings`` and
    ``errors`` are separate lists and are NEVER merged; hand-level warnings
    stay on each ``ParsedHand.warnings``.
    """

    hands: tuple[ParsedHand, ...]
    warnings: tuple[ParseIssue, ...]
    errors: tuple[ParseIssue, ...]
    source: RawSourceRef

    def __post_init__(self) -> None:
        hands = tuple(self.hands)
        if not all(isinstance(hand, ParsedHand) for hand in hands):
            raise TypeError("ParseResult.hands must contain ParsedHand instances")
        warnings = tuple(self.warnings)
        if not all(
            isinstance(issue, ParseIssue) and issue.kind == "warning" for issue in warnings
        ):
            raise ValueError(
                "ParseResult.warnings must contain 'warning'-kind ParseIssue instances"
            )
        errors = tuple(self.errors)
        if not all(
            isinstance(issue, ParseIssue) and issue.kind == "error" for issue in errors
        ):
            raise ValueError(
                "ParseResult.errors must contain 'error'-kind ParseIssue instances"
            )
        if not isinstance(self.source, RawSourceRef):
            raise TypeError(
                f"ParseResult.source must be a RawSourceRef, got {type(self.source).__name__}"
            )
        if errors and hands:
            raise ValueError(
                "ParseResult with errors must contain no hands (never trust partial "
                "input enough to emit hands alongside errors)"
            )
        object.__setattr__(self, "hands", hands)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "errors", errors)

    @property
    def ok(self) -> bool:
        """True exactly when ``errors`` is empty (may still have zero hands)."""
        return not self.errors


@runtime_checkable
class HandAdapter(Protocol):
    """Protocol every venue-specific parser adapter must satisfy.

    Fabrication rule (binding on all implementers): ``parse`` NEVER
    synthesizes actions, cards, hands or showdown outcomes that the raw text
    does not contain. Unparseable or truncated input returns a ``ParseResult``
    with ``errors`` populated and zero hands; tolerable deviations are
    recorded as ``warnings`` and never silently normalized away.

    ``CONTRACT_VERSION`` is the parser's contract version and MUST equal the
    ``poker.ingest.contracts.CONTRACT_VERSION`` it was compiled against; the
    ingest pipeline records it as ``hands.parser_version`` (dedup key — see
    the module docstring for version-bump semantics).
    """

    name: str
    CONTRACT_VERSION: str

    def parse(self, raw_text: str, source_name: str) -> ParseResult: ...
