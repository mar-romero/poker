"""Deterministic hand state machine: strict replay, snapshots, StateResult.

Task POKER-STATE-001 (planner STEP 1 API freeze, risk R2). Public API:

* ``STATE_VERSION = '1.0.0'`` — engine-owned cache-key constant
  (``EVALUATOR_VERSION='1.0.0'`` precedent; bump on any snapshot-semantics
  change, not refactors).
* ``DecisionSnapshot`` — frozen dataclass with EXACTLY 12 keys
  (A1): ``schema_version`` (domain SCHEMA_VERSION, validated via
  ``game.require_schema_version``), ``state_version`` (checked against
  ``STATE_VERSION``), ``street`` (enum name on the wire), ``action_index``,
  ``actor_to_act``, ``legal_actions``, ``active_players``, ``pot``,
  ``effective_stack``, ``amount_to_call``, ``min_raise``,
  ``is_betting_round_closed``. ``to_dict``/``from_dict`` round-trip
  byte-stable (``json.dumps(payload, sort_keys=True)``): integer chip units
  only (never floats), enums serialize as names (A2), mapping fields are
  ``MappingProxyType`` snapshots, exact-key rejection via
  ``game.require_exact_keys``.
* ``StateResult`` — final cumulative contributions, uncalled returns, and the
  side-pot view delegated to ``pots.build_pots``'s frozen
  ``PotBuildResult`` (exposed as ``.pots``).
* ``StateEngine.replay(table, blinds, actions, *, button_seat)`` —
  ``(list[DecisionSnapshot], StateResult)``.
* Error catalog (all ``ValueError`` subclasses, every message names the
  actor, street, violated invariant and expected values — planner STEP 9 /
  AC2): ``WrongTurnError``, ``WrongStreetError``, ``DuplicateActionError``,
  ``InsufficientStackError``, ``MinRaiseViolationError``,
  ``IllegalActionKindError``, ``InconsistentHistoryError``.

Pinned semantics (U1-U6 reconciliation, mirrored by the test suite):

* U1 ``action_index``: 0-based index of the LAST APPLIED action. A snapshot
  is emitted at every decision point that is actually reached: before each
  voluntary action is applied, plus one terminal snapshot when the hand ends
  (last fold, or every remaining active player all-in). Blind/ante POSTs are
  forced and produce no snapshot. The provenance prefix of a snapshot with
  ``action_index = i`` is ``actions[:i + 1]``.
* U2: ``amount_to_call`` is 0 for matched and all-in players; the round is
  closed when every non-folded player has acted since the last aggression or
  is all-in; a terminal fold ends the hand (no further decision points).
* U3 POST-beyond-stack PIN (STANDARD ruleset): a short blind/ante post that
  exceeds the poster's stack is an all-in blind post which is allowed and
  clamps at the remaining stack; a POST amount that otherwise deviates from
  one of {sb, bb, ante} is an ``InconsistentHistoryError``, never a silent
  contribution. (Planner STEP 4 vs brief conflict reconciled in favor of the
  standard short-all-in-blind rule.)
* ``button_seat`` (recorded contract addition): ``domain.game.TableMetadata``
  (SCHEMA_VERSION 1) carries players/seats/blinds but NO button seat, so
  ``StateEngine.replay`` requires ``button_seat: int`` to derive the
  clockwise order, blind roles and rotation starts. Clockwise order is
  ascending seat number; pairing of ``TableMetadata.players`` and
  ``.seats`` is positional (index).
* Heads-up pin (planner R3/U5, test-designer C05): BTN==SB posts sb, then
  the other player posts bb; BTN acts first preflop and first postflop.
  NOTE the postflop rule "start at the SB seat" is the test-designer pin and
  diverges from standard poker (non-button first postflop); recorded as a
  deliberate convention, not a silent bug (see docs/decisions/).
* Blind/ante inference (R/U-rule, A6): POST carries no post_type; the engine
  infers the role from the seat binding (SB seat / BB seat derived from the
  button) with a deterministic poster-order tie-break for ``sb == bb``,
  HEADS-UP: first poster is always BTN=SB. POST is legal only on
  Street.PREFLOP; the amount must match the role's blind value (or clamp as
  the all-in short stack), or an ante when ``ante > 0`` (exactly one ante per
  player in the preflop batch, otherwise ``InconsistentHistoryError``).
* Min-raise ladder (A5): (a) min bet = one bb or the shorter remaining
  stack; (b) min raise increment = max prior aggressive increment this
  street (+ to-call), i.e. raise-to >= top + max(ladder, bb); (c) an all-in
  below the min-raise increment is legal but does NOT reopen betting for
  players who already acted this street (no second decision points); (d) an
  all-in >= min-raise reopens betting. DELIBERATE SIMPLIFICATION: the full
  tournament reopening side-pot exception (WSOP-Rule-33 / TDA style, where
  callers below the sub-min all-in may still call back into the pot) is NOT
  implemented; documented trade-off in this docstring, decision journal and
  ``docs/decisions/`` — minus-raise reopen callers implicitly keep their
  matched amount and the all-in excess flows out through the standard
  uncalled-return accounting in pots.py.
* Strict replay (A8): actions are consumed strictly in list order with
  strictly increasing ``sequence`` numbers; the engine NEVER re-sorts by
  sequence (deliberate divergence from ``pots.normalize_contributions``,
  whose lenient sort would silently fold an out-of-order history).
* Street semantics: a new street may only arrive when the current betting
  round is CLOSED, and only as ``current + 1`` (skip or regression ->
  ``WrongStreetError``). Chip-bearing actions reference their own street's
  ``in_front`` exactly like normalize_contributions (A4/C18).
* Board scope resolution (explorer R2/U6, planner STEP 8):
  ``DECISION_ENGINE_SPEC`` lists board cards in the snapshot but the task
  JSON does not; board replay is OUT OF SCOPE here (owned by ingest/
  decision-spot tasks that own card data). The frozen 12-key schema leaves a
  schema_version/STATE_VERSION bump as the extension path; stale consumers
  fail closed via exact-key rejection.

Byte-stability: no floats, no sets in payloads; ``to_dict`` sorts mapping
keys; snapshot construction is a pure function of the action prefix; runtime
errors live outside the payload (explorer R5).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from poker.domain.game import (
    SCHEMA_VERSION,
    Action,
    ActionKind,
    AmountSemantics,
    BlindAnteConfig,
    Street,
    TableMetadata,
    require_exact_keys,
    require_schema_version,
)
from poker.domain.money import Chips
from poker.domain.pots import (
    PotBuildResult,
    UncalledReturn,
    build_pots,
    compute_uncalled_returns,
)
from poker.engine import rules

__all__ = [
    "STATE_VERSION",
    "DecisionSnapshot",
    "StateEngine",
    "StateResult",
    "WrongTurnError",
    "WrongStreetError",
    "DuplicateActionError",
    "InsufficientStackError",
    "MinRaiseViolationError",
    "IllegalActionKindError",
    "InconsistentHistoryError",
]

STATE_VERSION = "1.0.0"
"""Engine-owned snapshot-semantics cache key ('1.0.0')."""

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

_RESULT_KEYS = frozenset(
    {"schema_version", "contributions", "uncalled", "pots"}
)

_AMOUNTLESS_KINDS = frozenset({ActionKind.FOLD, ActionKind.CHECK})


# ---------------------------------------------------------------------------
# Error catalog (AC2): every message names actor, street, invariant, expected
# ---------------------------------------------------------------------------


def _violation(error_cls, actor, street, detail, expected) -> str:
    return (
        f"{error_cls.__name__}: invariant violated by actor {actor!r} on "
        f"street {street}: {detail}; expected {expected}"
    )


class WrongTurnError(ValueError):
    """The acting player is not the player the rotation mandates."""


class WrongStreetError(ValueError):
    """Street skip ahead, street regression, or advance while unresolved."""


class DuplicateActionError(ValueError):
    """A player acts twice before the betting round closes."""


class InsufficientStackError(ValueError):
    """A chip-bearing action adds more chips than the stack still holds."""


class MinRaiseViolationError(ValueError):
    """A bet/raise is below the pinned min-bet/min-raise ladder (A5)."""


class IllegalActionKindError(ValueError):
    """The action kind is illegal at this decision point / street."""


class InconsistentHistoryError(ValueError):
    """The history itself is impossible (batch counts, ordering, semantics)."""


# ---------------------------------------------------------------------------
# DecisionSnapshot (A1/B06)
# ---------------------------------------------------------------------------


def _frozen_mapping(value, label: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or isinstance(value, str):
        raise TypeError(f"{label} must be a name -> int mapping")
    coerced: dict[str, int] = {}
    for name, number in value.items():
        if type(name) is not str or not name.strip():
            raise ValueError(f"{label} keys must be non-empty player names")
        if type(number) is not int and not isinstance(number, Chips):
            raise TypeError(f"{label}[{name!r}] must be a plain int")
        coerced[name] = (
            number if type(number) is int else int(number.value)
        )
    return MappingProxyType(dict(sorted(coerced.items())))


def _kind_from_name(name: object) -> ActionKind:
    try:
        return ActionKind[name]  # type: ignore[index]
    except (KeyError, TypeError):
        raise ValueError(f"unknown legal action kind name: {name!r}") from None


@dataclasses.dataclass(frozen=True)
class DecisionSnapshot:
    """Immutable snapshot at one reached decision point (exact 12 keys)."""

    schema_version: int
    state_version: str
    street: Street
    action_index: int
    actor_to_act: str | None
    legal_actions: tuple
    active_players: tuple
    pot: int
    effective_stack: Mapping
    amount_to_call: Mapping
    min_raise: int
    is_betting_round_closed: bool

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool):
            raise TypeError("DecisionSnapshot.schema_version must be a plain int")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported snapshot schema_version {self.schema_version!r} "
                f"(expected {SCHEMA_VERSION})"
            )
        if type(self.state_version) is not str or not self.state_version:
            raise TypeError("DecisionSnapshot.state_version must be a non-empty str")
        if self.state_version != STATE_VERSION:
            raise ValueError(
                f"unsupported snapshot state_version {self.state_version!r} "
                f"(expected {STATE_VERSION!r})"
            )
        if not isinstance(self.street, Street):
            raise TypeError(
                "DecisionSnapshot.street must be a Street, got "
                f"{type(self.street).__name__}"
            )
        if type(self.action_index) is not int or isinstance(self.action_index, bool):
            raise TypeError("DecisionSnapshot.action_index must be a plain int")
        if self.action_index < -1:
            raise ValueError("DecisionSnapshot.action_index must be at least -1")
        if self.actor_to_act is not None and (
            type(self.actor_to_act) is not str or not self.actor_to_act.strip()
        ):
            raise TypeError(
                "DecisionSnapshot.actor_to_act must be None or a non-empty str"
            )
        legal_actions = tuple(self.legal_actions)
        if not all(isinstance(kind, ActionKind) for kind in legal_actions):
            raise TypeError(
                "DecisionSnapshot.legal_actions must contain ActionKind instances"
            )
        object.__setattr__(self, "legal_actions", tuple(legal_actions))
        active_players = tuple(self.active_players)
        if not all(type(name) is str and name.strip() for name in active_players):
            raise TypeError(
                "DecisionSnapshot.active_players must contain non-empty names"
            )
        object.__setattr__(self, "active_players", active_players)
        if type(self.pot) is not int or isinstance(self.pot, bool):
            raise TypeError("DecisionSnapshot.pot must be a plain int")
        if self.pot < 0:
            raise ValueError("DecisionSnapshot.pot must be non-negative")
        object.__setattr__(self, "effective_stack", _frozen_mapping(self.effective_stack, "effective_stack"))
        object.__setattr__(self, "amount_to_call", _frozen_mapping(self.amount_to_call, "amount_to_call"))
        if type(self.min_raise) is not int or isinstance(self.min_raise, bool):
            raise TypeError("DecisionSnapshot.min_raise must be a plain int")
        if self.min_raise < 0:
            raise ValueError("DecisionSnapshot.min_raise must be non-negative")
        if type(self.is_betting_round_closed) is not bool:
            raise TypeError(
                "DecisionSnapshot.is_betting_round_closed must be a bool"
            )

    def to_dict(self) -> dict:
        """Exact-key byte-stable payload (12 keys, no floats, no sets)."""
        return {
            "schema_version": self.schema_version,
            "state_version": self.state_version,
            "street": self.street.name,
            "action_index": self.action_index,
            "actor_to_act": self.actor_to_act,
            "legal_actions": [kind.name for kind in self.legal_actions],
            "active_players": list(self.active_players),
            "pot": self.pot,
            "effective_stack": {
                name: self.effective_stack[name]
                for name in sorted(self.effective_stack)
            },
            "amount_to_call": {
                name: self.amount_to_call[name]
                for name in sorted(self.amount_to_call)
            },
            "min_raise": self.min_raise,
            "is_betting_round_closed": self.is_betting_round_closed,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "DecisionSnapshot":
        """Fail-closed reconstruction: exact keys + version guard."""
        require_schema_version(payload)
        require_exact_keys(payload, _SNAPSHOT_KEYS, "DecisionSnapshot")
        if payload["state_version"] != STATE_VERSION:
            raise ValueError(
                f"unsupported state_version {payload['state_version']!r} "
                f"(expected {STATE_VERSION!r})"
            )
        street_name = payload["street"]
        try:
            street = Street[street_name]
        except (KeyError, TypeError):
            raise ValueError(f"unknown street name: {street_name!r}") from None
        try:
            legal_actions = tuple(
                _kind_from_name(name) for name in payload["legal_actions"]
            )
        except TypeError:
            raise ValueError(
                "DecisionSnapshot 'legal_actions' must be a list of kind names"
            ) from None
        active = payload["active_players"]
        effective = _plain_int_names(payload["effective_stack"], "effective_stack")
        to_call = _plain_int_names(payload["amount_to_call"], "amount_to_call")
        return cls(
            payload["schema_version"],
            payload["state_version"],
            street,
            payload["action_index"],
            payload["actor_to_act"],
            legal_actions,
            tuple(active),
            payload["pot"],
            effective,
            to_call,
            payload["min_raise"],
            payload["is_betting_round_closed"],
        )


def _plain_int_names(value: object, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise TypeError(f"DecisionSnapshot {label!r} must be a dict")
    return dict(value)


# ---------------------------------------------------------------------------
# StateResult (U6: final contributions, returned stacks, side-pot view)
# ---------------------------------------------------------------------------


def _chips_mapping_frozen(value: Mapping[str, Chips]) -> Mapping[str, Chips]:
    if not isinstance(value, Mapping) or isinstance(value, str):
        raise TypeError("contributions must be a name -> Chips mapping")
    coerced: dict[str, Chips] = {}
    for name, chips in value.items():
        if type(name) is not str or not name.strip():
            raise ValueError("contribution keys must be non-empty player names")
        if not isinstance(chips, Chips):
            raise TypeError(
                f"contributions[{name!r}] must be Chips, got {type(chips).__name__}"
            )
        coerced[name] = chips
    return MappingProxyType(dict(sorted(coerced.items())))


@dataclasses.dataclass(frozen=True)
class StateResult:
    """Final replay state: cumulative contributions, uncalled returns and the
    delegated ``PotBuildResult`` side-pot view (``.pots``)."""

    contributions: Mapping
    uncalled: tuple
    pots: PotBuildResult

    def __post_init__(self) -> None:
        if not isinstance(self.pots, PotBuildResult):
            raise TypeError(
                "StateResult.pots must be a PotBuildResult, got "
                f"{type(self.pots).__name__}"
            )
        uncalled = tuple(self.uncalled)
        if not all(isinstance(row, UncalledReturn) for row in uncalled):
            raise TypeError("StateResult.uncalled must contain UncalledReturn rows")
        object.__setattr__(self, "contributions", _chips_mapping_frozen(self.contributions))
        object.__setattr__(self, "uncalled", uncalled)

    @property
    def returned_stacks(self) -> tuple:
        """Returned (uncalled) chips as (player, Chips) rows — alias."""
        return self.uncalled

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "contributions": {
                name: self.contributions[name].to_dict()
                for name in sorted(self.contributions)
            },
            "uncalled": [row.to_dict() for row in self.uncalled],
            "pots": self.pots.to_dict(),
        }


# ---------------------------------------------------------------------------
# StateEngine
# ---------------------------------------------------------------------------


class _Replay:
    """Single-writer replay state (private; owned by :class:`StateEngine`)."""

    def __init__(self, table: TableMetadata, blinds: BlindAnteConfig, button_seat: int):
        self.blinds = blinds
        self.sb_value = blinds.sb.value
        self.bb_value = blinds.bb.value
        self.ante_value = blinds.ante.value
        table_blinds = table.blinds
        if (
            table_blinds.sb.value != self.sb_value
            or table_blinds.bb.value != self.bb_value
            or table_blinds.ante.value != self.ante_value
        ):
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    "replay",
                    "configuration",
                    "replay blinds differ from the table's own blind config",
                    "identical sb/bb/ante values",
                )
            )
        self.order = rules.seat_clockwise_order([seat.number for seat in table.seats])
        if type(button_seat) is not int or isinstance(button_seat, bool):
            raise TypeError("button_seat must be a plain int seat number")
        if button_seat not in self.order:
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    "replay",
                    "configuration",
                    f"button seat {button_seat} is not among table seats",
                    f"one of {sorted(self.order)}",
                )
            )
        self.holders = rules.seat_holders(table.players, table.seats)
        self.names = tuple(self.holders[seat] for seat in self.order)
        if len(set(self.names)) < 2 or len(self.names) != len(self.holders):
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    "replay",
                    "configuration",
                    "at least two distinct seated players are required",
                    "2..10 players with unique names/seats",
                )
            )
        self.player_count = len(self.names)
        self.seat_of = {name: seat for seat, name in self.holders.items()}
        self.sb_seat, self.bb_seat = rules.derive_blind_seats(
            button_seat, self.order, self.player_count
        )
        self.heads_up = self.player_count == 2
        self.stack_amount = {player.name: player.stack.value for player in table.players}
        self.street = Street.PREFLOP
        self.in_front = {name: 0 for name in self.names}
        self.contributed = {name: 0 for name in self.names}
        self.folded: set[str] = set()
        self.allin: set[str] = set()
        self.acted: set[str] = set()
        self.top = 0
        self.max_increment = self.bb_value  # preflop street floor (A5 b)
        self.round_closed = False
        self.terminal = False
        self.expected: str | None = None
        self.sb_actor: str | None = None
        self.bb_actor: str | None = None
        self.ante_actors: set[str] = set()
        self.last_index = -1
        self.last_seq = -1
        self.last_actor: str | None = None
        self.snapshots: list[DecisionSnapshot] = []

    # -- terrain helpers ---------------------------------------------------

    def _walk(self, start_seat: int, include_start: bool) -> str | None:
        """First player clockwise from *start_seat* skipping folded/all-in
        actors and players already acted (subject to include_start)."""
        total = len(self.order)
        position = self.order.index(start_seat)
        for offset in range(0 if include_start else 1, total):
            seat = self.order[(position + offset) % total]
            name = self.holders[seat]
            if name in self.folded or name in self.allin or name in self.acted:
                continue
            return name
        return None

    def _remaining(self, actor: str) -> int:
        return self.stack_amount[actor] - self.contributed[actor]

    def _to_call(self, actor: str) -> int:
        if actor in self.folded or actor in self.allin:
            return 0
        return max(self.top - self.in_front[actor], 0)

    def _active_names(self) -> tuple:
        return tuple(name for name in self.names if name not in self.folded)

    def _refresh_terminal(self) -> None:
        active = [name for name in self._active_names()]
        if len(active) <= 1 or all(name in self.allin for name in active):
            self.terminal = True
            self.round_closed = True
            self.expected = None

    # -- street / blind batch ---------------------------------------------

    def _advance_street(self, street: Street) -> None:
        self.street = street
        self.in_front = {name: 0 for name in self.names}
        self.acted = set()
        self.top = 0
        self.max_increment = 0
        self.round_closed = False
        self.last_actor = None  # the consecutive-action rule is per round
        if street is Street.PREFLOP:  # unreachable; defensive
            self.max_increment = self.bb_value
        self.expected = self._walk(self.sb_seat, include_start=True)

    def _blind_batch_open(self) -> bool:
        if self.sb_actor is None or self.bb_actor is None:
            return True
        return (
            self.ante_value > 0 and len(self.ante_actors) < self.player_count
        )

    def _require_batch(self, actor: str) -> None:
        if self.sb_actor is None or self.bb_actor is None:
            holder_sb = self.holders[self.sb_seat]
            holder_bb = self.holders[self.bb_seat]
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    self.street.name,
                    "incomplete preflop blind batch (betting may not start "
                    "before both blinds are posted)",
                    (
                        f"sb {self.sb_value} from {holder_sb!r} and bb "
                        f"{self.bb_value} from {holder_bb!r}"
                    ),
                )
            )
        if self.ante_value > 0 and len(self.ante_actors) < self.player_count:
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    self.street.name,
                    f"incomplete ante batch: {len(self.ante_actors)} ante POSTs "
                    f"seen for {self.player_count} players",
                    f"{self.player_count} ante POSTs of {self.ante_value} "
                    "(one per player)",
                )
            )

    def _book_post_chips(self, actor: str, amount: int) -> None:
        contribution = min(amount, self._remaining(actor))
        self.contributed[actor] += contribution
        self.in_front[actor] += contribution
        self.top = max(self.top, self.in_front[actor])
        if self.contributed[actor] == self.stack_amount[actor]:
            self.allin.add(actor)  # U3: all-in blind post clamp

    def _apply_post(self, action: Action, index: int) -> None:
        actor, amount = action.actor, action.amount.value
        if actor not in self.seat_of:
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    "PREFLOP",
                    f"POST by unseated actor {actor!r}",
                    f"one of {list(self.names)}",
                )
            )
        holder_sb = self.holders[self.sb_seat]
        holder_bb = self.holders[self.bb_seat]
        slot = None
        if actor == holder_sb and self.sb_actor is None:
            slot = ("sb", self.sb_value)
        elif actor == holder_bb and self.bb_actor is None:
            slot = ("bb", self.bb_value)
        is_ante_amount = (
            self.ante_value > 0
            and amount == self.ante_value
            and not (slot is not None and amount == slot[1])
        )
        if is_ante_amount:
            if actor in self.ante_actors:
                raise InconsistentHistoryError(
                    _violation(
                        InconsistentHistoryError,
                        actor,
                        "PREFLOP",
                        f"duplicate ante POST of {self.ante_value} by {actor!r} "
                        "(one ante per player)",
                        f"exactly {self.player_count} distinct ante posters",
                    )
                )
            self._book_post_chips(actor, amount)
            self.ante_actors.add(actor)
        elif slot is not None:
            target = slot[1]
            remaining_before = self._remaining(actor)
            clamped = amount == remaining_before and remaining_before < target
            if amount != target and not clamped:
                raise InconsistentHistoryError(
                    _violation(
                        InconsistentHistoryError,
                        actor,
                        "PREFLOP",
                        f"POST amount {amount} does not match the "
                        f"{slot[0]} blind (allowed blind values: sb "
                        f"{self.sb_value}, bb {self.bb_value}; blind roles: "
                        f"sb->{holder_sb!r}, bb->{holder_bb!r}; a short "
                        "all-in blind post must equal the remaining stack)",
                        f"{target} (or an all-in stack below it)",
                    )
                )
            self._book_post_chips(actor, amount)
            if slot[0] == "sb":
                self.sb_actor = actor
            else:
                self.bb_actor = actor
        elif self.sb_actor is not None and actor in (self.sb_actor, self.bb_actor):
            raise DuplicateActionError(
                _violation(
                    DuplicateActionError,
                    actor,
                    "PREFLOP",
                    f"{actor!r} already posted a blind in this batch",
                    "a different player, or a voluntary action",
                )
            )
        elif amount in (self.sb_value, self.bb_value):
            expected_holder = holder_sb if self.sb_actor is None else holder_bb
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    "PREFLOP",
                    f"{actor!r} is not the blind slot holder for the amount "
                    f"{amount} (blind roles: sb->{holder_sb!r}, "
                    f"bb->{holder_bb!r})",
                    f"blind POST from {expected_holder!r} first",
                )
            )
        else:
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    "PREFLOP",
                    f"POST amount {amount} matches no configured blind value "
                    f"(sb {self.sb_value} / bb {self.bb_value} / ante "
                    f"{self.ante_value}): it is never silently accepted as a "
                    "contribution",
                    f"one of sb {self.sb_value}, bb {self.bb_value}, ante "
                    f"{self.ante_value}",
                )
            )
        if not self._blind_batch_open():
            self.expected = self._walk(self.bb_seat, include_start=False)
            self.round_closed = self.expected is None
        self.last_index = index
        self.last_seq = action.sequence
        self.last_actor = actor

    # -- snapshots ---------------------------------------------------------

    def _decision_snapshot(self) -> DecisionSnapshot:
        actor = self.expected
        assert actor is not None
        remaining = self._remaining(actor)
        to_call = self._to_call(actor)
        if self.top == 0:
            min_raise = rules.min_bet_value(self.bb_value, remaining)
        else:
            min_raise = rules.min_raise_to_value(
                self.top, self.max_increment, self.bb_value
            )
        effective = {
            name: self.stack_amount[name] - self.contributed[name]
            for name in self.names
        }
        to_call_map = {}
        for name in self.names:
            if name in self.folded or name in self.allin:
                to_call_map[name] = 0
            else:
                to_call_map[name] = self._to_call(name)
        kinds = rules.legal_action_kinds(to_call, self.top, remaining)
        return DecisionSnapshot(
            SCHEMA_VERSION,
            STATE_VERSION,
            self.street,
            self.last_index,
            actor,
            tuple(kinds),
            self._active_names(),
            sum(self.contributed.values()),
            effective,
            to_call_map,
            min_raise,
            False,
        )

    def _terminal_snapshot(self) -> DecisionSnapshot:
        to_call_map = {}
        for name in self.names:
            to_call_map[name] = self._to_call(name)
        effective = {
            name: self.stack_amount[name] - self.contributed[name]
            for name in self.names
        }
        return DecisionSnapshot(
            SCHEMA_VERSION,
            STATE_VERSION,
            self.street,
            self.last_index,
            None,
            (),
            self._active_names(),
            sum(self.contributed.values()),
            effective,
            to_call_map,
            0,
            True,
        )

    # -- voluntary actions -------------------------------------------------

    def _consume_voluntary(self, index: int, action: Action) -> None:
        actor = action.actor
        kind = action.kind
        if kind is ActionKind.POST:
            raise IllegalActionKindError(
                _violation(
                    IllegalActionKindError,
                    actor,
                    self.street.name,
                    "POST is only legal inside the preflop blind/ante batch",
                    "a preflop blind/ante POST",
                )
            )
        if actor not in self.seat_of:
            raise WrongTurnError(
                _violation(
                    WrongTurnError,
                    actor,
                    self.street.name,
                    f"unknown actor {actor!r}: not seated at this table",
                    f"one of {list(self.names)}",
                )
            )
        to_call = self._to_call(actor)
        tried = self._remaining(actor)
        if actor == self.last_actor:
            raise DuplicateActionError(
                _violation(
                    DuplicateActionError,
                    actor,
                    self.street.name,
                    f"{actor!r} would act twice before the round closes "
                    f"(last action was also by {actor!r})",
                    f"{self.expected!r} to act (strictly one acted set "
                    "between aggressions; last applied action index "
                    f"{self.last_index})",
                )
            )
        if actor in self.folded:
            raise WrongTurnError(
                _violation(
                    WrongTurnError,
                    actor,
                    self.street.name,
                    f"{actor!r} has folded and may no longer act",
                    f"{self.expected!r} to act",
                )
            )
        if actor in self.acted:
            raise DuplicateActionError(
                _violation(
                    DuplicateActionError,
                    actor,
                    self.street.name,
                    f"{actor!r} already acted since the last aggression and "
                    "would act twice before the round closes",
                    f"{self.expected!r} to act (acted since aggression: "
                    f"{sorted(self.acted)})",
                )
            )
        if actor != self.expected:
            raise WrongTurnError(
                _violation(
                    WrongTurnError,
                    actor,
                    self.street.name,
                    f"{actor!r} is not the expected actor (last action by "
                    f"{self.last_actor!r}, applied action index "
                    f"{self.last_index})",
                    f"{self.expected!r} to act",
                )
            )
        snapshot = self._decision_snapshot()
        if kind in _AMOUNTLESS_KINDS:
            if kind is ActionKind.CHECK and to_call != 0:
                raise IllegalActionKindError(
                    _violation(
                        IllegalActionKindError,
                        actor,
                        self.street.name,
                        f"CHECK with {to_call} chips outstanding",
                        f"call {rules.required_call_amount(to_call, tried)} "
                        "chips (or fold) before checking",
                    )
                )
            # FOLD is legal at any open decision point (incl. fold-with-option);
            # CHECK requires zero outstanding to-call
            if kind is ActionKind.FOLD:
                self.folded.add(actor)
            self.acted.add(actor)
            self.expected = self._walk(self.seat_of[actor], include_start=False)
            self.round_closed = self.expected is None
            self.last_index, self.last_seq, self.last_actor = (
                index,
                action.sequence,
                actor,
            )
            self.snapshots.append(snapshot)
            self._refresh_terminal()
            return
        # chip-bearing action
        delta = rules.amount_delta(
            action.amount.value, action.amount_semantics, self.in_front[actor]
        )
        if delta is None:
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    self.street.name,
                    f"TOTAL_TO {action.amount.value} is below the "
                    f"{self.in_front[actor]} chips already in front "
                    "(impossible history)",
                    f"TOTAL_TO >= {self.in_front[actor] + to_call}",
                )
            )
        if delta > tried:
            raise InsufficientStackError(
                _violation(
                    InsufficientStackError,
                    actor,
                    self.street.name,
                    f"{kind.name} adds {delta} chips but only {tried} remain "
                    f"in the stack (contributed so far "
                    f"{self.contributed[actor]})",
                    f"at most {tried} chips",
                )
            )
        if kind is ActionKind.CALL:
            if to_call == 0:
                raise IllegalActionKindError(
                    _violation(
                        IllegalActionKindError,
                        actor,
                        self.street.name,
                        f"CALL with nothing to call (top {self.top}, "
                        f"in front {self.in_front[actor]})",
                        "CHECK (or a legal RAISE over the top)",
                    )
                )
            required = rules.required_call_amount(to_call, tried)
            if delta != required:
                raise IllegalActionKindError(
                    _violation(
                        IllegalActionKindError,
                        actor,
                        self.street.name,
                        f"CALL amount {delta} does not match the required "
                        f"{required} chips (top {self.top}, in front "
                        f"{self.in_front[actor]})",
                        f"call exactly {required} chips (all-in shortfall "
                        f"allowed: {min(to_call, tried)})",
                    )
                )
            self.acted.add(actor)
        elif kind is ActionKind.BET:
            if self.top != 0:
                raise IllegalActionKindError(
                    _violation(
                        IllegalActionKindError,
                        actor,
                        self.street.name,
                        f"BET over a live top of {self.top} chips is illegal "
                        "on this street",
                        "RAISE (a BET may only open a street)",
                    )
                )
            floor = rules.min_bet_value(self.bb_value, tried)
            if delta != tried and delta < floor:
                raise MinRaiseViolationError(
                    _violation(
                        MinRaiseViolationError,
                        actor,
                        self.street.name,
                        f"opening BET of {delta} chips is below the minimum "
                        f"bet (all-in requires exactly {tried})",
                        f"minimum {floor} chips (or all-in for the remaining "
                        f"{tried})",
                    )
                )
            if delta >= floor:
                self.max_increment = max(self.max_increment, delta)
                self.acted = {actor}
            else:  # short all-in bet
                self.acted.add(actor)
        elif kind is ActionKind.RAISE:
            if self.top == 0:
                raise IllegalActionKindError(
                    _violation(
                        IllegalActionKindError,
                        actor,
                        self.street.name,
                        f"RAISE with no live bet on {self.street.name} "
                        f"(current top {self.top})",
                        "BET (a RAISE must exceed a live top)",
                    )
                )
            new_top = self.in_front[actor] + delta
            if new_top <= self.top:
                raise IllegalActionKindError(
                    _violation(
                        IllegalActionKindError,
                        actor,
                        self.street.name,
                        f"raise-to {new_top} does not exceed the current top "
                        f"{self.top}",
                        f"raise to above {self.top} (to-call {to_call})",
                    )
                )
            required_increment = max(self.max_increment, self.bb_value)
            increment = new_top - self.top
            is_allin = delta == tried
            if increment < required_increment and not is_allin:
                raise MinRaiseViolationError(
                    _violation(
                        MinRaiseViolationError,
                        actor,
                        self.street.name,
                        f"raise-to {new_top} (increment {increment}) is below "
                        f"the minimum raise (min-raise ladder); to-call {to_call}",
                        f"raise to at least "
                        f"{rules.min_raise_to_value(self.top, self.max_increment, self.bb_value)} "
                        f"(increment at least {required_increment}; to-call "
                        f"{to_call}; all-in shortfall {tried} is the only "
                        "sub-min exception)",
                    )
                )
            if increment >= required_increment:
                self.max_increment = max(self.max_increment, increment)
                self.acted = {actor}  # (d) full raise reopens betting
            else:
                self.acted.add(actor)  # (c) sub-min all-in: no reopen
            self.top = new_top
        else:  # pragma: no cover - domains gates unknown kinds
            raise IllegalActionKindError(
                _violation(
                    IllegalActionKindError,
                    actor,
                    self.street.name,
                    f"unknown action kind {kind!r}",
                    "FOLD/CHECK/CALL/BET/RAISE/POST",
                )
            )
        # shared chip accounting + rotation refresh
        self.contributed[actor] += delta
        self.in_front[actor] += delta
        if kind in (ActionKind.BET, ActionKind.RAISE):
            self.top = self.in_front[actor]  # new live top
        if self.contributed[actor] == self.stack_amount[actor]:
            self.allin.add(actor)
        self.expected = self._walk(self.seat_of[actor], include_start=False)
        self.round_closed = self.expected is None
        self.last_index, self.last_seq, self.last_actor = (
            index,
            action.sequence,
            actor,
        )
        self.snapshots.append(snapshot)
        self._refresh_terminal()

    # -- dispatcher ---------------------------------------------------------

    def consume(self, index: int, action: Action) -> None:
        seq = action.sequence
        if seq <= self.last_seq:
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    action.actor,
                    action.street.name,
                    f"sequence {seq} does not strictly exceed the last "
                    f"applied sequence {self.last_seq}: the engine replays "
                    "in list order and never re-sorts (A8)",
                    f"sequences strictly increasing from "
                    f"{self.last_seq + 1}",
                )
            )
        if action.kind is ActionKind.POST and action.street is not Street.PREFLOP:
            raise IllegalActionKindError(
                _violation(
                    IllegalActionKindError,
                    action.actor,
                    action.street.name,
                    "POST is only legal inside the preflop blind/ante batch "
                    f"(got a POST on {action.street.name})",
                    f"a POST on Street.PREFLOP (actor may act on "
                    f"{action.street.name} with other kinds)",
                )
            )
        if action.street is not self.street:
            if action.street < self.street:
                raise WrongStreetError(
                    _violation(
                        WrongStreetError,
                        action.actor,
                        action.street.name,
                        f"street regression: {action.street.name} precedes "
                        f"the active street {self.street.name}",
                        f"streets strictly forward from {self.street.name}",
                    )
                )
            if not self.round_closed:
                raise WrongStreetError(
                    _violation(
                        WrongStreetError,
                        action.actor,
                        self.street.name,
                        f"street advance to {action.street.name} while the "
                        f"{self.street.name} betting round is unresolved",
                        f"close the {self.street.name} round before "
                        f"{action.street.name}",
                    )
                )
            if action.street != Street(self.street + 1):
                raise WrongStreetError(
                    _violation(
                        WrongStreetError,
                        action.actor,
                        action.street.name,
                        f"street skip: {action.street.name} jumps over "
                        f"{Street(self.street + 1).name}",
                        f"next street {Street(self.street + 1).name}",
                    )
                )
            self._advance_street(action.street)
            if self.expected is None:
                self.round_closed = True
        if self.round_closed:
            active_names = list(self._active_names())
            if active_names and all(
                name in self.allin for name in active_names
            ):
                remaining_note = (
                    "every remaining active player is all-in so no bettable "
                    "player remains"
                )
                next_street = "showdown (terminal state; no decision points)"
            else:
                remaining_note = "advance the street or end the hand"
                next_street = (
                    "showdown (terminal state; no decision points)"
                    if self.street is Street.RIVER
                    else Street(self.street + 1).name
                )
            raise IllegalActionKindError(
                _violation(
                    IllegalActionKindError,
                    action.actor,
                    self.street.name,
                    f"{action.kind.name} on a closed {self.street.name} "
                    f"round (a closed street does not reopen for more "
                    f"betting; {remaining_note})",
                    f"actions only on {next_street}",
                )
            )
        batch_open = self._blind_batch_open()
        if batch_open:
            if action.kind is ActionKind.POST:
                self._apply_post(action, index)
                return
            self._require_batch(action.actor)
        self._consume_voluntary(index, action)

    def finish(self) -> None:
        if self.last_index >= 0:
            self._require_batch_end()
        if self.terminal:
            self.snapshots.append(self._terminal_snapshot())

    def _require_batch_end(self) -> None:
        actor = "replay-end"
        if self.sb_actor is None or self.bb_actor is None:
            holder_sb = self.holders[self.sb_seat]
            holder_bb = self.holders[self.bb_seat]
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    self.street.name,
                    "incomplete preflop blind batch at end of replay "
                    "(betting already consumed actions)",
                    (
                        f"sb {self.sb_value} from {holder_sb!r} and bb "
                        f"{self.bb_value} from {holder_bb!r}"
                    ),
                )
            )
        if self.ante_value > 0 and len(self.ante_actors) < self.player_count:
            raise InconsistentHistoryError(
                _violation(
                    InconsistentHistoryError,
                    actor,
                    self.street.name,
                    f"incomplete ante batch at end of replay: "
                    f"{len(self.ante_actors)} ante POSTs seen for "
                    f"{self.player_count} players",
                    f"{self.player_count} ante POSTs of {self.ante_value}",
                )
            )


class StateEngine:
    """Strict replay entry point (no instances needed; classmethod only)."""

    @classmethod
    def replay(
        cls,
        table: TableMetadata,
        blinds: BlindAnteConfig,
        actions: Sequence[Action],
        *,
        button_seat: int,
    ) -> tuple[list[DecisionSnapshot], StateResult]:
        """Replay normalized actions street by street.

        ``table`` supplies players/seats; ``blinds`` must match
        ``table.blinds``; ``button_seat`` (REQUIRED, see module docstring) is
        the engine-level contract addition because TableMetadata carries no
        button seat. Returns ``(list[DecisionSnapshot], StateResult)``.
        Strict replay: actions consumed in list order with strictly
        increasing sequences; never re-sorted (A8).
        """
        if not isinstance(table, TableMetadata):
            raise TypeError(
                f"table must be a TableMetadata, got {type(table).__name__}"
            )
        if not isinstance(blinds, BlindAnteConfig):
            raise TypeError(
                f"blinds must be a BlindAnteConfig, got {type(blinds).__name__}"
            )
        if not isinstance(actions, (list, tuple)) or any(
            not isinstance(action, Action) for action in actions
        ):
            raise TypeError(
                "actions must be a list or tuple of canonical Action instances"
            )
        machine = _Replay(table, blinds, button_seat)
        for index, action in enumerate(actions):
            machine.consume(index, action)
        machine.finish()
        stacks = {
            name: Chips(stack) for name, stack in machine.stack_amount.items()
        }
        pots_result = build_pots(list(actions), stacks)
        result = StateResult(
            contributions=pots_result.contributions,
            uncalled=pots_result.uncalled,
            pots=pots_result,
        )
        return machine.snapshots, result
