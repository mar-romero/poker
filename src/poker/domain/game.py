"""Immutable canonical game-state domain types.

Structural validation only: this module deliberately contains no legal-action
engine. Serialization is versioned (``SCHEMA_VERSION``, the single version
source for the whole domain) and deterministic; every monetary amount is
encoded by ``Chips`` as ``{'schema_version', 'value', 'unit': 'chips'}`` with
a plain non-negative ``int`` value. Payloads never contain floats or sets and
use only tuples/ordered dicts, so ``json.dumps(payload, sort_keys=True)`` is
byte-stable across cycles.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping
from types import MappingProxyType

from poker.domain.money import Chips

__all__ = [
    "SCHEMA_VERSION",
    "Action",
    "ActionKind",
    "AmountSemantics",
    "BlindAnteConfig",
    "Player",
    "Position",
    "Pot",
    "Seat",
    "Street",
    "TableMetadata",
    "require_exact_keys",
    "require_schema_version",
]

SCHEMA_VERSION = 1


class AmountSemantics(enum.Enum):
    """How an action's chip amount must be interpreted."""

    ADDED = "added"
    TOTAL_TO = "total_to"


class ActionKind(enum.Enum):
    """Canonical action kinds."""

    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    POST = "post"


class Street(enum.IntEnum):
    """Ordered betting streets."""

    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3


class Position(enum.Enum):
    """Canonical table positions."""

    BTN = "btn"
    SB = "sb"
    BB = "bb"
    UTG = "utg"
    UTG1 = "utg1"
    UTG2 = "utg2"
    MP = "mp"
    LJ = "lj"
    HJ = "hj"
    CO = "co"


def require_schema_version(payload: object) -> int:
    """Validate that *payload* carries the current schema version, fail closed."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    if "schema_version" not in payload:
        raise ValueError("payload is missing required key 'schema_version'")
    version = payload["schema_version"]
    if type(version) is not int:
        raise ValueError("payload 'schema_version' must be a plain int")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {version!r} (expected {SCHEMA_VERSION})"
        )
    return version


def require_exact_keys(payload: dict, keys: frozenset[str], label: str) -> None:
    """Reject payloads with missing or unexpected keys (fail closed)."""
    if not isinstance(payload, dict):
        raise TypeError(f"{label} payload must be a dict")
    missing = keys - payload.keys()
    if missing:
        raise ValueError(f"{label} payload is missing keys: {sorted(missing)}")
    extra = payload.keys() - keys
    if extra:
        raise ValueError(f"{label} payload has unexpected keys: {sorted(extra)}")


def _coerce_chips(value: object, label: str) -> Chips:
    """Accept Chips or a plain int (shape-level coercion; no validation yet)."""
    if isinstance(value, Chips):
        return value
    if type(value) is int:
        return Chips(value)
    raise TypeError(f"{label} must be Chips or a plain int, got {type(value).__name__}")


def _enum_from_name(enum_cls: type, name: object, label: str):
    try:
        return enum_cls[name]
    except (KeyError, TypeError):
        raise ValueError(f"unknown {label}: {name!r}") from None


_PLAYER_KEYS = frozenset({"schema_version", "name", "stack"})
_SEAT_KEYS = frozenset({"schema_version", "number"})
_BLINDS_KEYS = frozenset({"schema_version", "sb", "bb", "ante"})
_ACTION_KEYS = frozenset(
    {
        "schema_version",
        "actor",
        "street",
        "kind",
        "amount",
        "amount_semantics",
        "sequence",
        "provenance",
    }
)
_POT_KEYS = frozenset({"schema_version", "contributions", "rake"})
_TABLE_KEYS = frozenset({"schema_version", "players", "seats", "blinds"})

_AMOUNTLESS_KINDS = frozenset({ActionKind.FOLD, ActionKind.CHECK})


@dataclasses.dataclass(frozen=True)
class Player:
    """A named player holding a chip stack."""

    name: str
    stack: Chips

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("Player.name must be a non-empty string")
        if not isinstance(self.stack, Chips):
            raise TypeError(
                f"Player.stack must be Chips, got {type(self.stack).__name__}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "stack": self.stack.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Player":
        require_schema_version(payload)
        require_exact_keys(payload, _PLAYER_KEYS, "Player")
        return cls(payload["name"], Chips.from_dict(payload["stack"]))


@dataclasses.dataclass(frozen=True)
class Seat:
    """A physical table seat, numbered 2..10."""

    number: int

    def __post_init__(self) -> None:
        if type(self.number) is not int:
            raise TypeError(
                f"Seat.number must be a plain int, got {type(self.number).__name__}"
            )
        if not 2 <= self.number <= 10:
            raise ValueError(f"Seat.number must be between 2 and 10, got {self.number}")

    def to_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "number": self.number}

    @classmethod
    def from_dict(cls, payload: object) -> "Seat":
        require_schema_version(payload)
        require_exact_keys(payload, _SEAT_KEYS, "Seat")
        return cls(payload["number"])


@dataclasses.dataclass(frozen=True)
class BlindAnteConfig:
    """Small blind / big blind / ante configuration in chip units."""

    sb: Chips
    bb: Chips
    ante: Chips

    def __post_init__(self) -> None:
        # Shape: accept Chips or plain ints; Chips enforces non-negativity.
        object.__setattr__(self, "sb", _coerce_chips(self.sb, "sb"))
        object.__setattr__(self, "bb", _coerce_chips(self.bb, "bb"))
        object.__setattr__(self, "ante", _coerce_chips(self.ante, "ante"))
        if self.sb.value > self.bb.value:
            raise ValueError(
                f"small blind {self.sb.value} must not exceed big blind {self.bb.value}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "sb": self.sb.to_dict(),
            "bb": self.bb.to_dict(),
            "ante": self.ante.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "BlindAnteConfig":
        require_schema_version(payload)
        require_exact_keys(payload, _BLINDS_KEYS, "BlindAnteConfig")
        return cls(
            Chips.from_dict(payload["sb"]),
            Chips.from_dict(payload["bb"]),
            Chips.from_dict(payload["ante"]),
        )


@dataclasses.dataclass(frozen=True)
class Action:
    """One player action with actor, street, kind, amount semantics,
    sequence order and source provenance."""

    actor: str
    street: Street
    kind: ActionKind
    amount: Chips | None
    amount_semantics: AmountSemantics | None
    sequence: int
    provenance: str

    def __post_init__(self) -> None:
        if type(self.actor) is not str or not self.actor.strip():
            raise ValueError("Action.actor must be a non-empty string")
        if not isinstance(self.street, Street):
            raise TypeError(
                f"Action.street must be a Street, got {type(self.street).__name__}"
            )
        if not isinstance(self.kind, ActionKind):
            raise TypeError(
                f"Action.kind must be an ActionKind, got {type(self.kind).__name__}"
            )
        if self.amount is not None and not isinstance(self.amount, Chips):
            raise TypeError(
                f"Action.amount must be Chips or None, got {type(self.amount).__name__}"
            )
        if self.amount_semantics is not None and not isinstance(
            self.amount_semantics, AmountSemantics
        ):
            raise TypeError(
                "Action.amount_semantics must be an AmountSemantics or None, got "
                f"{type(self.amount_semantics).__name__}"
            )
        if type(self.sequence) is not int:
            raise TypeError(
                f"Action.sequence must be a plain int, got {type(self.sequence).__name__}"
            )
        if self.sequence < 0:
            raise ValueError("Action.sequence must be non-negative")
        if type(self.provenance) is not str or not self.provenance.strip():
            raise ValueError("Action.provenance must be a non-empty string")
        if self.kind in _AMOUNTLESS_KINDS:
            if self.amount is not None or self.amount_semantics is not None:
                raise ValueError(
                    f"{self.kind.name} must not carry an amount or amount semantics"
                )
        else:
            if self.amount is None:
                raise ValueError(f"{self.kind.name} must carry a Chips amount")
            if self.amount_semantics is None:
                raise ValueError(f"{self.kind.name} must carry amount semantics")

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "actor": self.actor,
            "street": self.street.name,
            "kind": self.kind.name,
            "amount": None if self.amount is None else self.amount.to_dict(),
            "amount_semantics": (
                None if self.amount_semantics is None else self.amount_semantics.name
            ),
            "sequence": self.sequence,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Action":
        require_schema_version(payload)
        require_exact_keys(payload, _ACTION_KEYS, "Action")
        return cls(
            payload["actor"],
            _enum_from_name(Street, payload["street"], "street"),
            _enum_from_name(ActionKind, payload["kind"], "kind"),
            None if payload["amount"] is None else Chips.from_dict(payload["amount"]),
            (
                None
                if payload["amount_semantics"] is None
                else _enum_from_name(
                    AmountSemantics, payload["amount_semantics"], "amount_semantics"
                )
            ),
            payload["sequence"],
            payload["provenance"],
        )


@dataclasses.dataclass(frozen=True)
class Pot:
    """A pot with per-player contributions and separate rake."""

    contributions: Mapping[str, Chips]
    rake: Chips = Chips(0)

    def __post_init__(self) -> None:
        if not isinstance(self.contributions, Mapping) or isinstance(
            self.contributions, str
        ):
            raise TypeError(
                "Pot.contributions must be a mapping of player name to Chips"
            )
        contributions: dict[str, Chips] = {}
        for name, value in self.contributions.items():
            if type(name) is not str or not name.strip():
                raise ValueError(
                    "Pot.contributions keys must be non-empty player names"
                )
            if not isinstance(value, Chips):
                raise TypeError(
                    f"Pot.contributions[{name!r}] must be Chips, got "
                    f"{type(value).__name__}"
                )
            contributions[name] = value
        object.__setattr__(self, "contributions", MappingProxyType(contributions))
        if not isinstance(self.rake, Chips):
            raise TypeError(
                f"Pot.rake must be Chips, got {type(self.rake).__name__}"
            )

    @property
    def total(self) -> Chips:
        """Total chips in the pot: contributions plus rake."""
        return Chips(
            sum(value.value for value in self.contributions.values()) + self.rake.value
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "contributions": {
                name: self.contributions[name].to_dict()
                for name in sorted(self.contributions)
            },
            "rake": self.rake.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Pot":
        require_schema_version(payload)
        require_exact_keys(payload, _POT_KEYS, "Pot")
        contributions = payload["contributions"]
        if not isinstance(contributions, dict):
            raise TypeError("Pot payload 'contributions' must be a dict")
        return cls(
            {name: Chips.from_dict(node) for name, node in contributions.items()},
            Chips.from_dict(payload["rake"]),
        )


@dataclasses.dataclass(frozen=True)
class TableMetadata:
    """Table metadata: seated players, their seats and the blind/ante config."""

    players: tuple[Player, ...]
    seats: tuple[Seat, ...]
    blinds: BlindAnteConfig

    def __post_init__(self) -> None:
        players = tuple(self.players)
        seats = tuple(self.seats)
        if not all(isinstance(player, Player) for player in players):
            raise TypeError("TableMetadata.players must contain Player instances")
        if not all(isinstance(seat, Seat) for seat in seats):
            raise TypeError("TableMetadata.seats must contain Seat instances")
        if not isinstance(self.blinds, BlindAnteConfig):
            raise TypeError(
                f"TableMetadata.blinds must be a BlindAnteConfig, got "
                f"{type(self.blinds).__name__}"
            )
        if len(players) != len(seats):
            raise ValueError("TableMetadata requires exactly one seat per player")
        numbers = [seat.number for seat in seats]
        if len(set(numbers)) != len(numbers):
            raise ValueError("TableMetadata seat numbers must be unique")
        names = [player.name for player in players]
        if len(set(names)) != len(names):
            raise ValueError("TableMetadata player names must be unique")
        object.__setattr__(self, "players", players)
        object.__setattr__(self, "seats", seats)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "players": [player.to_dict() for player in self.players],
            "seats": [seat.to_dict() for seat in self.seats],
            "blinds": self.blinds.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "TableMetadata":
        require_schema_version(payload)
        require_exact_keys(payload, _TABLE_KEYS, "TableMetadata")
        if not isinstance(payload["players"], list) or not isinstance(
            payload["seats"], list
        ):
            raise TypeError("TableMetadata payload 'players'/'seats' must be lists")
        return cls(
            tuple(Player.from_dict(node) for node in payload["players"]),
            tuple(Seat.from_dict(node) for node in payload["seats"]),
            BlindAnteConfig.from_dict(payload["blinds"]),
        )
