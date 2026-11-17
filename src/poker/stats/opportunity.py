"""Pure opportunity evaluation over ``StateEngine.replay`` artifacts (D1).

Task POKER-STATS-OPPORTUNITY-001 (planner D1-D8 pins; risk R2, TDD).
Predicates are evaluated PURELY from replay artifacts (the DecisionSnapshot
list plus the Action sequence): no ORM/database imports, no IO, no
wall-clock, no randomness, no dict-iteration-order dependence. Statistics
modules import only ``poker.domain.game``, ``poker.engine.state`` and the
pure vocabulary in ``poker.features.schema`` (``build_positions``).

Pinned semantics (planner D2/D3/D4/D5; the six predicate definitions with
their documented tracker-convention divergences live in
:mod:`poker.stats.definitions`):

* D3 (all-in/no-decision exclusion, ``is_decision_opportunity``): an
  opportunity exists only if (i) the actor is present and among
  ``active_players``, (ii) ``effective_stack[actor] > 0`` (not already
  all-in) and (iii) ``legal_actions`` contains at least one kind other than
  FOLD. Facing-all-in ``{FOLD, CALL}`` states REMAIN opportunities;
  terminal snapshots (actor None) never produce matches.
* D4 (heads-up/multiway, ``is_heads_up``): ``heads_up`` is
  ``len(active_players) == 2`` evaluated AT the decision point; fewer than
  2 active players fails closed (ValueError, defensive under strict
  replay).
* D5 (granularity): evaluation is PER DECISION POINT. First-wins dedupe
  (the feature-table persistence concern in ``poker.features.materialize``)
  is deliberately NOT imported: re-opened decisions are distinct decision
  points, and one decision point may carry matches for several stats at
  once (e.g. a cold-call decision is both a three_bet and a
  fold_to_three_bet opportunity).
* Correspondence (U1/D8): the decided action of a snapshot is
  ``actions[snapshot.action_index + 1]`` and must exist, must not be a
  POST, and must belong to the snapshot actor, else ValueError
  (``decided_action``, fail-closed; not triggerable through the public API
  because ``evaluate_hand`` replays internally — defensive check).
* U5 (heads_up representation): ``heads_up`` is a dedicated bool field on
  OpportunityMatch; ``match.dimensions`` carries only
  position/opener_position (str -> str).
* ``OpportunityMatch.action_index`` is the index of the DECIDED action in
  the replayed action sequence (the opportunity's provenance action).

Purity contract: every function here is a pure function of its explicit
inputs; ``evaluate_hand`` performs no IO and returns frozen dataclasses.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from poker.domain.game import (
    Action,
    ActionKind,
    BlindAnteConfig,
    Street,
    TableMetadata,
)
from poker.engine.state import DecisionSnapshot, StateEngine
from poker.features.schema import build_positions
from poker.stats.definitions import list_definitions

__all__ = [
    "HandEvaluation",
    "OpportunityMatch",
    "StatCount",
    "decided_action",
    "evaluate_hand",
    "is_decision_opportunity",
    "is_heads_up",
]

_AGGRESSIVE_KINDS = frozenset({ActionKind.BET, ActionKind.RAISE})
_STEAL_POSITIONS = frozenset({"CO", "BTN", "SB"})
_STAT_ORDER = (
    "three_bet",
    "fold_to_three_bet",
    "cbet",
    "fold_to_cbet",
    "steal",
    "bb_defense",
)


@dataclasses.dataclass(frozen=True)
class OpportunityMatch:
    """One opportunity: every non-excluded predicate match is a match.

    ``successes`` are matches with ``succeeded=True`` and ``opportunities``
    are all matches, so a zero-opportunity stat yields zero matches and a
    ``StatCount.rate()`` of None (never 0.0 — catalog sample-display rule).
    """

    stat_id: str
    actor: str
    street: Street
    action_index: int
    succeeded: bool
    heads_up: bool
    dimensions: Mapping


@dataclasses.dataclass(frozen=True)
class StatCount:
    """successes/opportunities pair; ``rate()`` is None on 0 opportunities."""

    stat_id: str
    successes: int
    opportunities: int

    def rate(self):
        """Raw success rate, or None when there were no opportunities."""
        if self.opportunities == 0:
            return None
        return self.successes / self.opportunities


@dataclasses.dataclass(frozen=True)
class HandEvaluation:
    """All OpportunityMatch values of one replayed hand."""

    matches: tuple

    def count_by_stat(self):
        """{stat_id: StatCount} over ALL pinned definitions (zero-filled)."""
        counts = {
            definition.stat_id: StatCount(definition.stat_id, 0, 0)
            for definition in list_definitions()
        }
        for match in self.matches:
            count = counts[match.stat_id]
            counts[match.stat_id] = StatCount(
                match.stat_id,
                count.successes + (1 if match.succeeded else 0),
                count.opportunities + 1,
            )
        return counts


def is_decision_opportunity(snapshot):
    """D3 exclusion rule as one pure, testable helper.

    True iff the decision point offers a genuine strategic choice: the actor
    is present and active (rule i), not already all-in (rule ii) and the
    legal_actions contain at least one kind other than FOLD (rule iii).
    Facing-all-in ``{FOLD, CALL}`` decisions remain opportunities; terminal
    snapshots (actor None) never match.
    """
    if snapshot.actor_to_act is None:
        return False
    if snapshot.actor_to_act not in snapshot.active_players:
        return False
    if int(snapshot.effective_stack.get(snapshot.actor_to_act, 0)) <= 0:
        return False
    if not any(kind is not ActionKind.FOLD for kind in snapshot.legal_actions):
        return False
    return True


def is_heads_up(snapshot):
    """D4: heads-up iff exactly 2 active players at the decision point.

    Fewer than 2 active players fails closed (ValueError) — defensive only;
    under strict replay a decision point always has at least 2 active
    players.
    """
    active_count = len(tuple(snapshot.active_players))
    if active_count < 2:
        raise ValueError(
            "decision point has %d active players; heads-up/multiway "
            "classification requires at least 2" % (active_count,)
        )
    return active_count == 2


def decided_action(snapshot, actions):
    """Decided action for one acting snapshot (U1/D8, fail-closed).

    Returns ``(decided_index, action)`` where ``decided_index`` is the index
    of the decided action in the replayed action sequence. The successor of
    ``snapshot.action_index`` must exist, must not be a POST (posts produce
    no decision snapshots) and must belong to the snapshot actor, else
    ValueError.
    """
    if snapshot.actor_to_act is None:
        raise ValueError(
            "terminal snapshot (actor_to_act None) has no decided action"
        )
    index = snapshot.action_index + 1
    if not 0 <= index < len(actions):
        raise ValueError(
            "snapshot correspondence mismatch: snapshot.action_index %d has "
            "no successor action in a %d-action sequence"
            % (snapshot.action_index, len(actions))
        )
    action = actions[index]
    if action.kind is ActionKind.POST:
        raise ValueError(
            "snapshot correspondence mismatch: the action after "
            "snapshot.action_index %d is a POST, which produces no decision "
            "snapshot" % (snapshot.action_index,)
        )
    if action.actor != snapshot.actor_to_act:
        raise ValueError(
            "snapshot correspondence mismatch: snapshot actor %r but the "
            "next action's actor is %r" % (snapshot.actor_to_act, action.actor)
        )
    return index, action


def same_street_counts(actions_prefix, street):
    """(raise_count, call_count) on ``street`` strictly before the decision.

    POSTs never count as raises or calls; a postflop BET counts as the
    street's first raise-level action (materialize precedent).
    """
    raise_count = 0
    call_count = 0
    for action in actions_prefix:
        if action.street is not street:
            continue
        if action.kind in _AGGRESSIVE_KINDS:
            raise_count += 1
        elif action.kind is ActionKind.CALL:
            call_count += 1
    return raise_count, call_count


def same_street_last_aggressor(actions_prefix, street):
    """Actor of the LAST same-street BET/RAISE strictly before the decision.

    For ``Street.PREFLOP`` this is the preflop final aggressor identity
    required by the c-bet/fold-to-c-bet predicates (D2: initiative is
    structurally 0 under strict replay, so the identity is recomputed from
    the raw action sequence).
    """
    aggressor = None
    for action in actions_prefix:
        if action.street is street and action.kind in _AGGRESSIVE_KINDS:
            aggressor = action.actor
    return aggressor


@dataclasses.dataclass(frozen=True)
class _DecisionPoint:
    """Same-street enrichment of one non-excluded decision point."""

    snapshot: DecisionSnapshot
    decided_index: int
    action: Action
    actor: str
    position: str
    heads_up: bool
    raise_count: int
    call_count: int
    preflop_aggressor: str | None
    aggressor_position: str | None
    street_bettor: str | None


def _enrich(snapshot, actions, positions, name_to_seat):
    """Enrich one acting snapshot with everything the predicates need."""
    decided_index, action = decided_action(snapshot, actions)
    actor = snapshot.actor_to_act
    if actor not in name_to_seat:
        raise ValueError("actor %r has no seat on the replayed table" % (actor,))
    prefix = actions[:decided_index]
    raise_count, call_count = same_street_counts(prefix, snapshot.street)
    preflop_aggressor = same_street_last_aggressor(prefix, Street.PREFLOP)
    aggressor_position = None
    if preflop_aggressor is not None:
        aggressor_position = positions[name_to_seat[preflop_aggressor]]
    return _DecisionPoint(
        snapshot=snapshot,
        decided_index=decided_index,
        action=action,
        actor=actor,
        position=positions[name_to_seat[actor]],
        heads_up=is_heads_up(snapshot),
        raise_count=raise_count,
        call_count=call_count,
        preflop_aggressor=preflop_aggressor,
        aggressor_position=aggressor_position,
        street_bettor=same_street_last_aggressor(prefix, snapshot.street),
    )


def _opener_position_or_fail(point):
    """aggressor_position for rc==1 predicates (defensive fail-closed)."""
    if point.aggressor_position is None:  # pragma: no cover - defensive
        raise ValueError(
            "raise_count == 1 requires a preflop final aggressor, but none "
            "was found before the decision"
        )
    return point.aggressor_position


def _match_three_bet(point):
    """D2 (1): preflop raise over a LONE open; success = RAISE."""
    if point.snapshot.street is not Street.PREFLOP:
        return None
    if point.raise_count != 1 or point.call_count != 0:
        return None
    return OpportunityMatch(
        stat_id="three_bet",
        actor=point.actor,
        street=point.snapshot.street,
        action_index=point.decided_index,
        succeeded=point.action.kind is ActionKind.RAISE,
        heads_up=point.heads_up,
        dimensions={"position": point.position},
    )


def _match_fold_to_three_bet(point):
    """D2 (2): FOLD facing a lone open; opener_position = the opener."""
    if point.snapshot.street is not Street.PREFLOP:
        return None
    if point.raise_count != 1 or point.call_count != 0:
        return None
    return OpportunityMatch(
        stat_id="fold_to_three_bet",
        actor=point.actor,
        street=point.snapshot.street,
        action_index=point.decided_index,
        succeeded=point.action.kind is ActionKind.FOLD,
        heads_up=point.heads_up,
        dimensions={
            "position": point.position,
            "opener_position": _opener_position_or_fail(point),
        },
    )


def _match_cbet(point):
    """D2 (3): flop BET by the preflop final aggressor, flop rc == 0."""
    if point.snapshot.street is not Street.FLOP:
        return None
    if point.raise_count != 0:
        return None
    if point.preflop_aggressor is None or point.preflop_aggressor != point.actor:
        return None
    return OpportunityMatch(
        stat_id="cbet",
        actor=point.actor,
        street=point.snapshot.street,
        action_index=point.decided_index,
        succeeded=point.action.kind is ActionKind.BET,
        heads_up=point.heads_up,
        dimensions={"position": point.position},
    )


def _match_fold_to_cbet(point):
    """D2 (4): FOLD vs a TRUE c-bet (flop bettor IS the preflop aggressor)."""
    if point.snapshot.street is not Street.FLOP:
        return None
    if point.raise_count != 1:
        return None
    if point.street_bettor is None or point.street_bettor != point.preflop_aggressor:
        return None
    if point.actor == point.street_bettor:
        return None
    return OpportunityMatch(
        stat_id="fold_to_cbet",
        actor=point.actor,
        street=point.snapshot.street,
        action_index=point.decided_index,
        succeeded=point.action.kind is ActionKind.FOLD,
        heads_up=point.heads_up,
        dimensions={
            "position": point.position,
            "opener_position": _opener_position_or_fail(point),
        },
    )


def _match_steal(point):
    """D2 (5): unopened CO/BTN/SB raise attempt; success = RAISE."""
    if point.snapshot.street is not Street.PREFLOP:
        return None
    if point.raise_count != 0 or point.call_count != 0:
        return None
    if point.position not in _STEAL_POSITIONS:
        return None
    return OpportunityMatch(
        stat_id="steal",
        actor=point.actor,
        street=point.snapshot.street,
        action_index=point.decided_index,
        succeeded=point.action.kind is ActionKind.RAISE,
        heads_up=point.heads_up,
        dimensions={"position": point.position},
    )


def _match_bb_defense(point):
    """D2 (6): BB facing a single open (limped callers allowed); success =
    CALL or RAISE."""
    if point.snapshot.street is not Street.PREFLOP:
        return None
    if point.position != "BB" or point.raise_count != 1:
        return None
    return OpportunityMatch(
        stat_id="bb_defense",
        actor=point.actor,
        street=point.snapshot.street,
        action_index=point.decided_index,
        succeeded=point.action.kind in (ActionKind.CALL, ActionKind.RAISE),
        heads_up=point.heads_up,
        dimensions={"opener_position": _opener_position_or_fail(point)},
    )


_MATCHERS = {
    "three_bet": _match_three_bet,
    "fold_to_three_bet": _match_fold_to_three_bet,
    "cbet": _match_cbet,
    "fold_to_cbet": _match_fold_to_cbet,
    "steal": _match_steal,
    "bb_defense": _match_bb_defense,
}


def _stat_matches(point):
    """All pinned-stat matches for one decision point (fixed order)."""
    matches = []
    for stat_id in _STAT_ORDER:
        match = _MATCHERS[stat_id](point)
        if match is not None:
            matches.append(match)
    return matches


def evaluate_hand(table, blinds, actions, *, button_seat):
    """Evaluate the six pinned opportunity predicates for one replayed hand.

    Pure evaluation source (D1): the hand is replayed with
    ``StateEngine.replay`` and every non-excluded decision point is tested
    against every pinned predicate. Returns a ``HandEvaluation`` whose
    ``matches`` tuple contains one ``OpportunityMatch`` per predicate match
    in fixed registry order (deterministic; byte-identical re-runs).

    Fail-closed boundaries: non-``TableMetadata``/``BlindAnteConfig``
    arguments raise TypeError; an unknown button seat raises ValueError via
    ``features.build_positions`` BEFORE replay (D8); a non-Action sequence
    element propagates the engine's TypeError; illegal hands propagate the
    engine's replay errors (fix the fixture, never the rule).
    """
    if not isinstance(table, TableMetadata):
        raise TypeError(
            "table must be a TableMetadata, got %s" % type(table).__name__
        )
    if not isinstance(blinds, BlindAnteConfig):
        raise TypeError(
            "blinds must be a BlindAnteConfig, got %s" % type(blinds).__name__
        )
    if not isinstance(actions, (list, tuple)):
        raise TypeError(
            "actions must be a list or tuple of canonical Action instances"
        )
    seats = [seat.number for seat in table.seats]
    positions = build_positions(seats, button_seat)  # D8: fail closed first
    name_to_seat = {
        player.name: seat.number for player, seat in zip(table.players, table.seats)
    }
    snapshots, _result = StateEngine.replay(
        table, blinds, actions, button_seat=button_seat
    )
    matches = []
    for snapshot in snapshots:
        if not is_decision_opportunity(snapshot):
            continue  # D3 exclusions; terminal snapshots contribute nothing
        point = _enrich(snapshot, actions, positions, name_to_seat)
        matches.extend(_stat_matches(point))
    return HandEvaluation(matches=tuple(matches))
