"""Pure legality/sizing predicates for the replay state machine (POKER-STATE-001).

Planner STEP 1 freeze: this module holds PK-functions frozen at plan time —
position binding, blind/seat derivation, rotation starts, min-bet/min-raise
ladder math (A5), amount normalization (ADDED vs TOTAL_TO with street-scoped
``in_front`` like ``pots.normalize_contributions``), and the ``legal_actions``
pin (A7). Rules are pure: no I/O, no mutation of any action, no engine-error
raising — predicates return data (``None``/booleans are deterministic
markers) and the state machine in :mod:`poker.engine.state` turns violations
into the typed invariant errors with actionable context.

Position binding and its deliberate simplifications (recorded as ADR notes in
``state.py`` and ``docs/decisions/``):

* Clockwise order is ascending :class:`~poker.domain.game.Seat` number
  (seat 2..10). ``button_seat`` is a REQUIRED engine-level parameter because
  ``TableMetadata`` (domain SCHEMA_VERSION 1) carries no button seat.
* Small blind = the seat clockwise after the button; big blind = the seat
  after that; HEADS-UP exception: the button is the small blind and the
  other player is the big blind.
* Preflop rotation starts strictly clockwise after the big blind (the first
  voluntary actor).
* Postflop rotation starts AT the small-blind seat (walking clockwise,
  skipping folded/all-in players). In heads-up play this makes the button
  (= SB) act first postflop, which is the test-designer/planner pin (C05);
  standard poker tables the non-button player acts first there — the
  divergence is recorded, not silent.
* min bet = one big blind, or the player's remaining stack when shorter
  (all-in); min raise-to = current street top + max(max prior aggressive
  increment this street, big blind) (A5 a-b).
* ``amount_delta`` mirrors ``pots.normalize_contributions`` street-scoped
  ``in_front`` accounting: ADDED adds the full amount, TOTAL_TO adds
  ``amount - in_front`` (caller rejects a negative delta as an impossible
  history — same rule, divergent failure mode: strict replay raises, the
  pots pipeline re-sorts and only errors on impossible totals).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from poker.domain.game import ActionKind, AmountSemantics, Street

__all__ = [
    "amount_delta",
    "derive_blind_seats",
    "legal_action_kinds",
    "min_bet_value",
    "min_raise_to_value",
    "next_seat",
    "required_call_amount",
    "rotation_start",
    "seat_clockwise_order",
    "seat_holders",
]


def seat_clockwise_order(seat_numbers: Sequence[int]) -> tuple[int, ...]:
    """Ascending seat numbers: clockwise action order at a poker table."""
    return tuple(sorted(seat_numbers))


def next_seat(order: Sequence[int], seat: int) -> int:
    """The seat immediately clockwise of *seat* (wrapping)."""
    position = order.index(seat)
    return order[(position + 1) % len(order)]


def seat_holders(
    players: Sequence, seats: Sequence
) -> Mapping[int, str]:
    """Pair players with seats by index: seat number -> player name."""
    holders: dict[int, str] = {}
    for player, seat in zip(players, seats):
        holders[seat.number] = player.name
    return MappingProxyType(dict(sorted(holders.items())))


def derive_blind_seats(
    button_seat: int, order: Sequence[int], player_count: int
) -> tuple[int, int]:
    """Return ``(sb_seat, bb_seat)`` from the button seat.

    Heads-up (2 players) pins BTN=SB; otherwise SB is clockwise after BTN
    and BB after SB (planner STEP 4).
    """
    if player_count < 2:
        raise ValueError(
            f"derive_blind_seats requires at least 2 players, got {player_count}"
        )
    if button_seat not in order:
        raise ValueError(
            f"button seat {button_seat} is not among table seats "
            f"{sorted(order)}"
        )
    if player_count == 2:
        return button_seat, next_seat(order, button_seat)
    sb_seat = next_seat(order, button_seat)
    return sb_seat, next_seat(order, sb_seat)


def rotation_start(
    preflop: bool, bb_seat: int, sb_seat: int, order: Sequence[int]
) -> int:
    """The seat where the next betting round's first voluntary actor is sought.

    Preflop: strictly clockwise after the big blind (the first voluntary
    actor, e.g. UTG). Postflop: AT the small-blind seat (inclusive), which
    skips folded/all-in players later via the caller's walk.
    """
    if preflop:
        return next_seat(order, bb_seat)
    return sb_seat


def amount_delta(
    amount_value: int, semantics: AmountSemantics, in_front_value: int
) -> int | None:
    """Chips actually added by one chip-bearing action (A4).

    ADDED: the full amount is the delta. TOTAL_TO: the street-total target
    minus what the actor already has in front on the current street (same
    accounting as ``pots.normalize_contributions``). ``None`` marks an
    impossible history (TOTAL_TO below the chips already in front); the
    caller raises the typed engine error with actionable context.
    """
    if semantics is AmountSemantics.ADDED:
        return amount_value
    if semantics is AmountSemantics.TOTAL_TO:
        delta = amount_value - in_front_value
        return None if delta < 0 else delta
    raise ValueError(f"unknown amount semantics: {semantics!r}")


def required_call_amount(to_call_value: int, remaining_value: int) -> int:
    """Exact chips a CALL must add: the outstanding to-call, or the caller's
    remaining stack when going all-in called for less."""
    return min(to_call_value, remaining_value)


def min_bet_value(bb_value: int, remaining_value: int) -> int:
    """(a) Minimum opening bet: one big blind, or the remaining all-in
    stack when shorter (A5)."""
    return min(bb_value, remaining_value)


def min_raise_to_value(
    top_value: int, max_increment: int, bb_value: int
) -> int:
    """(b) Minimum legal raise-to total this street: the current top plus the
    largest requirement of the ladder (max prior aggressive increment, with a
    big-blind floor) (A5)."""
    return top_value + max(max_increment, bb_value)


def legal_action_kinds(
    to_call_value: int, top_value: int, remaining_value: int
) -> tuple[ActionKind, ...]:
    """A7 legal-actions pin, in the engine's canonical order.

    to-call > 0: FOLD, CALL, and RAISE only when the stack can add chips
    beyond the call. to-call == 0: CHECK, plus BET to open (top == 0) or
    RAISE over the live top (e.g. the preflop big-blind option). All-in
    players never reach a decision point (U2).
    """
    if to_call_value > 0:
        kinds: list[ActionKind] = [ActionKind.FOLD, ActionKind.CALL]
        if remaining_value > to_call_value:
            kinds.append(ActionKind.RAISE)
        return tuple(kinds)
    kinds = [ActionKind.CHECK]
    if top_value == 0:
        kinds.append(ActionKind.BET)
    else:
        kinds.append(ActionKind.RAISE)
    return tuple(kinds)
