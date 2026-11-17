"""Versioned preflop statistic catalog over ``StateEngine.replay`` artifacts.

Task POKER-STATS-PREFLOP-001 (risk R2, important_calculation; adopted plan
steps 3-4). This module owns the CLOSED registry of the 23 combined preflop
definitions — the six pinned definitions reused IDENTICALLY from
:mod:`poker.stats.definitions` (never re-declared) plus 17 new catalog
definitions under an independent version constant
``PREFLOP_DEFINITIONS_VERSION = '1.0.0'`` — and the pure evaluation that
composes them.

Machine-readable contract (AC1): every definition carries
``predicate_spec`` / ``success_spec`` / ``dimensions`` / ``exclusions`` via
``preflop_definitions_to_dict()``; the predicate vocabulary is CLOSED
(``PREFLOP_PREDICATE_VOCABULARY``) and pinned by tests. Percentage results
expose successes, opportunities, raw rate and ``definition_version`` through
the frozen ``StatRate`` view (AC3); ``raw_rate`` is ``None`` on zero
opportunities (never ``0.0`` — catalog sample-display rule).

Pinned predicate semantics (adopted plan; notation as in
``poker.stats.definitions``: raise/call counts are same-street counts
strictly before the decision):

* ``vpip``: the actor's FIRST non-excluded preflop decision; success = CALL
  or RAISE (a BB check is never a success).
* ``pfr``: same opportunity; success = RAISE. Documented pin: limp-then-raise
  is NEVER a PFR success (the raise happens at a re-opened second decision,
  which the first-decision gate excludes).
* ``rfi`` / ``open_limp`` / ``sb_complete``: unopened (rc == 0, cc == 0);
  success RAISE / CALL / CALL (SB only). ``sb_complete`` intentionally ALSO
  matches ``open_limp`` (D5 granularity: one decision point may carry
  matches for several stats).
* ``over_limp`` / ``iso_raise``: limped (rc == 0, cc >= 1); success
  CALL / RAISE.
* ``fold_to_iso`` / ``call_to_iso``: rc == 1 AND cc >= 1; success FOLD /
  CALL; ``opener_position`` is the iso-raiser's position (the last preflop
  raiser).
* ``cold_call``: rc == 1 AND cc == 0; success = CALL; ``opener_position`` is
  the lone opener's position.
* ``four_bet``: rc == 2 (regardless of cc); success = RAISE.
* ``five_bet`` / ``fold_to_four_bet`` / ``call_to_four_bet``: rc == 3;
  success RAISE / FOLD / CALL; ``opener_position`` is the last preflop
  raiser's position (the 4-bettor).
* ``squeeze``: rc == 1 AND cc >= 1; success = RAISE.
* ``fold_to_squeeze`` / ``call_to_squeeze``: rc == 2 AND cc >= 1; success
  FOLD / CALL; ``opener_position`` is the squizzer's position (the last
  preflop raiser).

Dimensions: ``(position, heads_up)`` for attempt stats;
``(position, opener_position, heads_up)`` for response stats. The
first-decision gate applies to ``vpip``/``pfr`` ONLY.

Composition (adopted plan step 4): ``evaluate_preflop_hand`` performs a
two-replay composition — replay #1 via the frozen ``poker.stats.evaluate_hand``
(the pinned six, listed FIRST in the combined matches) and replay #2 for the
17 catalog definitions. The composition is PURE: no ORM/database imports, no
IO, no wall-clock, no randomness; evaluation is a deterministic function of
its explicit inputs and composes ONLY public helpers (``StateEngine.replay``,
``is_decision_opportunity``, ``decided_action``, ``same_street_counts``,
``same_street_last_aggressor``, ``build_positions``). Frozen files
(``poker.stats.definitions`` / ``poker.stats.opportunity``) are consumed,
never modified.
"""

from __future__ import annotations

import dataclasses

from poker.domain.game import (
    ActionKind,
    BlindAnteConfig,
    Street,
    TableMetadata,
)
from poker.engine.state import StateEngine
from poker.features.schema import build_positions
from poker.stats.definitions import StatDefinition, list_definitions
from poker.stats.opportunity import (
    OpportunityMatch,
    StatCount,
    decided_action,
    evaluate_hand,
    is_decision_opportunity,
    is_heads_up,
    same_street_counts,
    same_street_last_aggressor,
)

__all__ = [
    "PREFLOP_DEFINITIONS_VERSION",
    "PREFLOP_PREDICATE_VOCABULARY",
    "PreflopEvaluation",
    "StatRate",
    "evaluate_preflop_hand",
    "get_preflop_definition",
    "list_preflop_definitions",
    "preflop_counts",
    "preflop_definitions_to_dict",
    "stat_rates",
]

PREFLOP_DEFINITIONS_VERSION = "1.0.0"
"""Definition-set version for the NEW catalog definitions (independent
constant; ``poker.stats.definitions.DEFINITIONS_VERSION`` stays byte-frozen
for the pinned six). Bump on any new-definition semantics change (D6)."""

# D3 exclusion rule, reused verbatim from poker.stats.definitions (frozen).
_EXCLUSIONS_D3 = (
    "D3 all-in/no-decision exclusions: the actor must be present "
    "(actor_to_act not None) and among active_players, must not already be "
    "all-in (effective_stack[actor] > 0), and the legal_actions must contain "
    "at least one kind other than FOLD; facing-all-in {FOLD, CALL} decisions "
    "REMAIN opportunities (fold-vs-shove is a genuine decision); terminal "
    "snapshots (actor None) never produce matches."
)

PREFLOP_PREDICATE_VOCABULARY = frozenset(
    {
        "street",
        "same_street_raise_count_before",
        "same_street_call_count_before",
        "same_street_call_count_min",
        "position_in",
        "actor_position",
        "first_preflop_decision",
        # Keys reused from the pinned definitions.py specs:
        "actor_is",
        "street_bettor_is",
        "actor_is_not",
    }
)

_UNOPENED_PREDICATE = {
    "street": "PREFLOP",
    "same_street_raise_count_before": 0,
    "same_street_call_count_before": 0,
}
_FIRST_DECISION_PREDICATE = {
    "street": "PREFLOP",
    "first_preflop_decision": True,
}

_DIMENSIONS_ATTEMPT = ("position", "heads_up")
_DIMENSIONS_RESPONSE = ("position", "opener_position", "heads_up")


def _new_definition(stat_id, description, predicate_spec, success_spec, dimensions):
    """One frozen new-definition value under the catalog version constant."""
    return StatDefinition(
        stat_id=stat_id,
        definition_version=PREFLOP_DEFINITIONS_VERSION,
        description=description,
        streets=frozenset({Street.PREFLOP}),
        predicate_spec=dict(predicate_spec),
        success_spec=dict(success_spec),
        dimensions=dimensions,
        exclusions=_EXCLUSIONS_D3,
    )


_VPIP_DEF = _new_definition(
    "vpip",
    (
        "Voluntarily put money in the pot: the actor's FIRST non-excluded "
        "preflop decision; success = decided CALL or RAISE (a BB check is "
        "never a vpip success; a re-opened second decision is never a vpip "
        "opportunity)."
    ),
    dict(_FIRST_DECISION_PREDICATE),
    {"decided_action": ["CALL", "RAISE"]},
    _DIMENSIONS_ATTEMPT,
)

_PFR_DEF = _new_definition(
    "pfr",
    (
        "Preflop raise: the actor's FIRST non-excluded preflop decision; "
        "success = decided RAISE. Documented pin: limp-then-raise is NEVER a "
        "PFR success (the raise occurs at a re-opened second decision, which "
        "the first-decision gate excludes)."
    ),
    dict(_FIRST_DECISION_PREDICATE),
    {"decided_action": "RAISE"},
    _DIMENSIONS_ATTEMPT,
)

_RFI_DEF = _new_definition(
    "rfi",
    (
        "Raise first in: an unopened preflop raise attempt (raise_count == 0 "
        "and call_count == 0 before the decision). Success = decided RAISE."
    ),
    dict(_UNOPENED_PREDICATE),
    {"decided_action": "RAISE"},
    _DIMENSIONS_ATTEMPT,
)

_OPEN_LIMP_DEF = _new_definition(
    "open_limp",
    (
        "Open limp: a CALL into an unopened preflop pot (raise_count == 0 "
        "and call_count == 0 before the decision). Success = decided CALL. "
        "Per the mechanical predicate the BB check decision is an "
        "opportunity whose CHECK is never a success."
    ),
    dict(_UNOPENED_PREDICATE),
    {"decided_action": "CALL"},
    _DIMENSIONS_ATTEMPT,
)

_SB_COMPLETE_DEF = _new_definition(
    "sb_complete",
    (
        "SB complete: the small blind CALLING to complete against an "
        "unopened preflop pot (raise_count == 0, call_count == 0, actor "
        "position == SB). Success = decided CALL. Intentionally ALSO matches "
        "open_limp at the same decision point (D5 granularity)."
    ),
    dict(_UNOPENED_PREDICATE, actor_position="SB"),
    {"decided_action": "CALL"},
    _DIMENSIONS_ATTEMPT,
)

_OVER_LIMP_DEF = _new_definition(
    "over_limp",
    (
        "Over limp: a CALL into an already-limped preflop pot (raise_count "
        "== 0 and call_count >= 1 before the decision). Success = decided "
        "CALL."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 0,
        "same_street_call_count_min": 1,
    },
    {"decided_action": "CALL"},
    _DIMENSIONS_ATTEMPT,
)

_ISO_RAISE_DEF = _new_definition(
    "iso_raise",
    (
        "Isolation raise: a RAISE over one or more limpers (raise_count == 0 "
        "and call_count >= 1 before the decision). Success = decided RAISE. "
        "Never a three_bet (call_count == 0 there) and never a steal "
        "success."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 0,
        "same_street_call_count_min": 1,
    },
    {"decided_action": "RAISE"},
    _DIMENSIONS_ATTEMPT,
)

_FOLD_TO_ISO_DEF = _new_definition(
    "fold_to_iso",
    (
        "Fold to isolation raise: a FOLD facing an iso-raise (raise_count == "
        "1 and call_count >= 1 before the decision). opener_position is the "
        "iso-raiser's position (the last preflop raiser). Success = decided "
        "FOLD."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 1,
        "same_street_call_count_min": 1,
    },
    {"decided_action": "FOLD"},
    _DIMENSIONS_RESPONSE,
)

_CALL_TO_ISO_DEF = _new_definition(
    "call_to_iso",
    (
        "Call to isolation raise: a CALL facing an iso-raise (raise_count == "
        "1 and call_count >= 1 before the decision). opener_position is the "
        "iso-raiser's position (the last preflop raiser). Success = decided "
        "CALL."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 1,
        "same_street_call_count_min": 1,
    },
    {"decided_action": "CALL"},
    _DIMENSIONS_RESPONSE,
)

_COLD_CALL_DEF = _new_definition(
    "cold_call",
    (
        "Cold call: a CALL facing a LONE preflop open with no limped callers "
        "(raise_count == 1 and call_count == 0 before the decision). "
        "opener_position is the lone opener's position. Success = decided "
        "CALL."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 1,
        "same_street_call_count_before": 0,
    },
    {"decided_action": "CALL"},
    _DIMENSIONS_RESPONSE,
)

_FOUR_BET_DEF = _new_definition(
    "four_bet",
    (
        "Four-bet: a RAISE facing two preflop raises (raise_count == 2 "
        "regardless of call_count). The actor's own re-opened second "
        "decision qualifies (no first-decision gate). Success = decided "
        "RAISE."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 2,
    },
    {"decided_action": "RAISE"},
    _DIMENSIONS_ATTEMPT,
)

_FIVE_BET_DEF = _new_definition(
    "five_bet",
    (
        "Five-bet: a RAISE facing three preflop raises (raise_count == 3). "
        "Success = decided RAISE. A fold or call FACING a five-bet "
        "(raise_count == 4) matches nothing: the FACING_ACTIONS "
        "'facing-4bet+' cap is an aggregation label only."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 3,
    },
    {"decided_action": "RAISE"},
    _DIMENSIONS_ATTEMPT,
)

_FOLD_TO_FOUR_BET_DEF = _new_definition(
    "fold_to_four_bet",
    (
        "Fold to four-bet: a FOLD facing a four-bet (raise_count == 3 before "
        "the decision). opener_position is the last preflop raiser's "
        "position (the 4-bettor). Success = decided FOLD."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 3,
    },
    {"decided_action": "FOLD"},
    _DIMENSIONS_RESPONSE,
)

_CALL_TO_FOUR_BET_DEF = _new_definition(
    "call_to_four_bet",
    (
        "Call to four-bet: a CALL facing a four-bet (raise_count == 3 before "
        "the decision). opener_position is the last preflop raiser's "
        "position (the 4-bettor). Success = decided CALL."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 3,
    },
    {"decided_action": "CALL"},
    _DIMENSIONS_RESPONSE,
)

_SQUEEZE_DEF = _new_definition(
    "squeeze",
    (
        "Squeeze: a RAISE over an open plus one or more limped callers "
        "(raise_count == 1 and call_count >= 1 before the decision). Success "
        "= decided RAISE."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 1,
        "same_street_call_count_min": 1,
    },
    {"decided_action": "RAISE"},
    _DIMENSIONS_ATTEMPT,
)

_FOLD_TO_SQUEEZE_DEF = _new_definition(
    "fold_to_squeeze",
    (
        "Fold to squeeze: a FOLD facing a squeeze (raise_count == 2 and "
        "call_count >= 1 before the decision). opener_position is the "
        "squizzer's position (the last preflop raiser). Success = decided "
        "FOLD."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 2,
        "same_street_call_count_min": 1,
    },
    {"decided_action": "FOLD"},
    _DIMENSIONS_RESPONSE,
)

_CALL_TO_SQUEEZE_DEF = _new_definition(
    "call_to_squeeze",
    (
        "Call to squeeze: a CALL facing a squeeze (raise_count == 2 and "
        "call_count >= 1 before the decision). opener_position is the "
        "squizzer's position (the last preflop raiser). Success = decided "
        "CALL."
    ),
    {
        "street": "PREFLOP",
        "same_street_raise_count_before": 2,
        "same_street_call_count_min": 1,
    },
    {"decided_action": "CALL"},
    _DIMENSIONS_RESPONSE,
)

_NEW_DEFINITIONS = (
    _VPIP_DEF,
    _PFR_DEF,
    _RFI_DEF,
    _OPEN_LIMP_DEF,
    _SB_COMPLETE_DEF,
    _OVER_LIMP_DEF,
    _ISO_RAISE_DEF,
    _FOLD_TO_ISO_DEF,
    _CALL_TO_ISO_DEF,
    _COLD_CALL_DEF,
    _FOUR_BET_DEF,
    _FIVE_BET_DEF,
    _FOLD_TO_FOUR_BET_DEF,
    _CALL_TO_FOUR_BET_DEF,
    _SQUEEZE_DEF,
    _FOLD_TO_SQUEEZE_DEF,
    _CALL_TO_SQUEEZE_DEF,
)

_CATALOG_ORDER = tuple(definition.stat_id for definition in _NEW_DEFINITIONS)


def list_preflop_definitions():
    """All 23 combined preflop definitions in fixed registry order.

    The first six entries are the FROZEN pinned definitions returned by
    ``poker.stats.definitions.list_definitions()`` (same objects, same
    order); the remaining 17 are the new catalog definitions in the plan's
    fixed order. Deterministic; no id collisions.
    """
    return tuple(list_definitions()) + _NEW_DEFINITIONS


_DEFINITIONS_BY_ID = {
    definition.stat_id: definition for definition in list_preflop_definitions()
}


def get_preflop_definition(stat_id):
    """One preflop definition by stat id; unknown ids fail closed."""
    try:
        return _DEFINITIONS_BY_ID[stat_id]
    except (KeyError, TypeError):
        raise ValueError("unknown statistic id: %r" % (stat_id,)) from None


def preflop_definitions_to_dict():
    """Introspectable {stat_id: payload} registry (machine-readable AC1)."""
    return {
        definition.stat_id: definition.to_dict()
        for definition in list_preflop_definitions()
    }


@dataclasses.dataclass(frozen=True)
class StatRate:
    """One percentage result: successes / opportunities / raw rate / version.

    ``raw_rate`` is ``None`` when there were no opportunities (never ``0.0``
    — catalog sample-display rule) and ``successes / opportunities``
    otherwise. Frozen (AC3).
    """

    stat_id: str
    definition_version: str
    successes: int
    opportunities: int
    raw_rate: float | None


@dataclasses.dataclass(frozen=True)
class PreflopEvaluation:
    """All combined matches of one replayed hand (pinned six FIRST)."""

    matches: tuple

    def count_by_stat(self):
        """{stat_id: StatCount} zero-filled over the full 23-def registry."""
        return preflop_counts(self.matches)

    def rates(self):
        """{stat_id: StatRate} carrying definition_version (AC3)."""
        return stat_rates(self.count_by_stat())


# ---------------------------------------------------------------------------
# Pure catalog evaluation (replay #2; public helpers only)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _DecisionPoint:
    """Same-street enrichment of one non-excluded decision point."""

    snapshot: object
    decided_index: int
    action: object
    actor: str
    position: str
    heads_up: bool
    raise_count: int
    call_count: int
    aggressor_position: str | None
    is_first_preflop_decision: bool


def _opener_position_or_fail(point):
    """aggressor_position for opener-keyed predicates (fail-closed)."""
    if point.aggressor_position is None:  # pragma: no cover - defensive
        raise ValueError(
            "opener-keyed predicate requires a same-street final aggressor, "
            "but none was found before the decision"
        )
    return point.aggressor_position


def _preflop_match(point, stat_id, succeeded, *, opener=False):
    """One OpportunityMatch for a catalog stat (fixed field semantics)."""
    dimensions = {"position": point.position}
    if opener:
        dimensions["opener_position"] = _opener_position_or_fail(point)
    return OpportunityMatch(
        stat_id=stat_id,
        actor=point.actor,
        street=point.snapshot.street,
        action_index=point.decided_index,
        succeeded=succeeded,
        heads_up=point.heads_up,
        dimensions=dimensions,
    )


def _is_preflop(point):
    return point.snapshot.street is Street.PREFLOP


def _match_vpip(point):
    if not _is_preflop(point) or not point.is_first_preflop_decision:
        return None
    succeeded = point.action.kind in (ActionKind.CALL, ActionKind.RAISE)
    return _preflop_match(point, "vpip", succeeded)


def _match_pfr(point):
    if not _is_preflop(point) or not point.is_first_preflop_decision:
        return None
    return _preflop_match(point, "pfr", point.action.kind is ActionKind.RAISE)


def _match_rfi(point):
    if not _is_preflop(point) or point.raise_count != 0 or point.call_count != 0:
        return None
    return _preflop_match(point, "rfi", point.action.kind is ActionKind.RAISE)


def _match_open_limp(point):
    if not _is_preflop(point) or point.raise_count != 0 or point.call_count != 0:
        return None
    return _preflop_match(point, "open_limp", point.action.kind is ActionKind.CALL)


def _match_sb_complete(point):
    if not _is_preflop(point) or point.raise_count != 0 or point.call_count != 0:
        return None
    if point.position != "SB":
        return None
    return _preflop_match(point, "sb_complete", point.action.kind is ActionKind.CALL)


def _match_over_limp(point):
    if not _is_preflop(point) or point.raise_count != 0 or point.call_count < 1:
        return None
    return _preflop_match(point, "over_limp", point.action.kind is ActionKind.CALL)


def _match_iso_raise(point):
    if not _is_preflop(point) or point.raise_count != 0 or point.call_count < 1:
        return None
    return _preflop_match(point, "iso_raise", point.action.kind is ActionKind.RAISE)


def _match_fold_to_iso(point):
    if not _is_preflop(point) or point.raise_count != 1 or point.call_count < 1:
        return None
    return _preflop_match(
        point, "fold_to_iso", point.action.kind is ActionKind.FOLD, opener=True
    )


def _match_call_to_iso(point):
    if not _is_preflop(point) or point.raise_count != 1 or point.call_count < 1:
        return None
    return _preflop_match(
        point, "call_to_iso", point.action.kind is ActionKind.CALL, opener=True
    )


def _match_cold_call(point):
    if not _is_preflop(point) or point.raise_count != 1 or point.call_count != 0:
        return None
    return _preflop_match(
        point, "cold_call", point.action.kind is ActionKind.CALL, opener=True
    )


def _match_four_bet(point):
    if not _is_preflop(point) or point.raise_count != 2:
        return None
    return _preflop_match(point, "four_bet", point.action.kind is ActionKind.RAISE)


def _match_five_bet(point):
    if not _is_preflop(point) or point.raise_count != 3:
        return None
    return _preflop_match(point, "five_bet", point.action.kind is ActionKind.RAISE)


def _match_fold_to_four_bet(point):
    if not _is_preflop(point) or point.raise_count != 3:
        return None
    return _preflop_match(
        point, "fold_to_four_bet", point.action.kind is ActionKind.FOLD, opener=True
    )


def _match_call_to_four_bet(point):
    if not _is_preflop(point) or point.raise_count != 3:
        return None
    return _preflop_match(
        point, "call_to_four_bet", point.action.kind is ActionKind.CALL, opener=True
    )


def _match_squeeze(point):
    if not _is_preflop(point) or point.raise_count != 1 or point.call_count < 1:
        return None
    return _preflop_match(point, "squeeze", point.action.kind is ActionKind.RAISE)


def _match_fold_to_squeeze(point):
    if not _is_preflop(point) or point.raise_count != 2 or point.call_count < 1:
        return None
    return _preflop_match(
        point, "fold_to_squeeze", point.action.kind is ActionKind.FOLD, opener=True
    )


def _match_call_to_squeeze(point):
    if not _is_preflop(point) or point.raise_count != 2 or point.call_count < 1:
        return None
    return _preflop_match(
        point, "call_to_squeeze", point.action.kind is ActionKind.CALL, opener=True
    )


_MATCHERS = {
    "vpip": _match_vpip,
    "pfr": _match_pfr,
    "rfi": _match_rfi,
    "open_limp": _match_open_limp,
    "sb_complete": _match_sb_complete,
    "over_limp": _match_over_limp,
    "iso_raise": _match_iso_raise,
    "fold_to_iso": _match_fold_to_iso,
    "call_to_iso": _match_call_to_iso,
    "cold_call": _match_cold_call,
    "four_bet": _match_four_bet,
    "five_bet": _match_five_bet,
    "fold_to_four_bet": _match_fold_to_four_bet,
    "call_to_four_bet": _match_call_to_four_bet,
    "squeeze": _match_squeeze,
    "fold_to_squeeze": _match_fold_to_squeeze,
    "call_to_squeeze": _match_call_to_squeeze,
}


def _enrich_point(snapshot, actions, positions, name_to_seat, first_decision_seen):
    """Enrich one non-excluded decision point (pure; public helpers only)."""
    decided_index, action = decided_action(snapshot, actions)
    actor = snapshot.actor_to_act
    if actor not in name_to_seat:
        raise ValueError("actor %r has no seat on the replayed table" % (actor,))
    prefix = actions[:decided_index]
    raise_count, call_count = same_street_counts(prefix, snapshot.street)
    is_first = False
    if snapshot.street is Street.PREFLOP and actor not in first_decision_seen:
        first_decision_seen.add(actor)  # membership set only; order-free
        is_first = True
    aggressor = same_street_last_aggressor(prefix, snapshot.street)
    aggressor_position = None
    if aggressor is not None:
        aggressor_position = positions[name_to_seat[aggressor]]
    return _DecisionPoint(
        snapshot=snapshot,
        decided_index=decided_index,
        action=action,
        actor=actor,
        position=positions[name_to_seat[actor]],
        heads_up=is_heads_up(snapshot),
        raise_count=raise_count,
        call_count=call_count,
        aggressor_position=aggressor_position,
        is_first_preflop_decision=is_first,
    )


def _evaluate_catalog_matches(table, blinds, actions, *, button_seat):
    """Replay #2: all 17 catalog matches for one hand, in fixed order."""
    seats = [seat.number for seat in table.seats]
    positions = build_positions(seats, button_seat)  # fail closed first
    name_to_seat = {
        player.name: seat.number for player, seat in zip(table.players, table.seats)
    }
    snapshots, _result = StateEngine.replay(
        table, blinds, actions, button_seat=button_seat
    )
    first_decision_seen = set()
    matches = []
    for snapshot in snapshots:
        if not is_decision_opportunity(snapshot):
            continue  # D3 exclusions; terminal snapshots contribute nothing
        point = _enrich_point(
            snapshot, actions, positions, name_to_seat, first_decision_seen
        )
        for stat_id in _CATALOG_ORDER:
            match = _MATCHERS[stat_id](point)
            if match is not None:
                matches.append(match)
    return tuple(matches)


def evaluate_preflop_hand(table, blinds, actions, *, button_seat):
    """Combined pinned-six + catalog-17 evaluation for one replayed hand.

    Two-replay composition (pure/deterministic/no-IO): replay #1 via the
    frozen ``poker.stats.evaluate_hand`` (pinned six, listed FIRST in the
    combined ``matches``) and replay #2 via ``_evaluate_catalog_matches``
    (17 catalog definitions in fixed registry order per decision point).
    Neither replay mutates any input; the result is a frozen
    ``PreflopEvaluation`` and byte-identical re-runs produce identical
    matches.

    Fail-closed boundaries: non-``TableMetadata``/``BlindAnteConfig``
    arguments raise TypeError; a non-(list/tuple) ``actions`` raises
    TypeError; an unknown button seat raises ValueError via
    ``features.build_positions`` BEFORE any replay; illegal hands propagate
    the engine's replay errors (fix the fixture, never the rule).
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
    pinned = evaluate_hand(table, blinds, actions, button_seat=button_seat)
    catalog_matches = _evaluate_catalog_matches(
        table, blinds, actions, button_seat=button_seat
    )
    return PreflopEvaluation(matches=pinned.matches + catalog_matches)


def preflop_counts(matches):
    """Zero-filled {stat_id: StatCount} over the full 23-def registry."""
    counts = {
        definition.stat_id: StatCount(definition.stat_id, 0, 0)
        for definition in list_preflop_definitions()
    }
    for match in matches:
        if match.stat_id not in counts:
            raise ValueError(
                "unknown statistic id in matches: %r" % (match.stat_id,)
            )
        count = counts[match.stat_id]
        counts[match.stat_id] = StatCount(
            match.stat_id,
            count.successes + (1 if match.succeeded else 0),
            count.opportunities + 1,
        )
    return counts


def stat_rates(counts):
    """{stat_id: StatRate} joining counts to definitions (AC3).

    Every rate carries the definition's ``definition_version``; a missing
    count for a registry id fails closed (ValueError).
    """
    rates = {}
    for definition in list_preflop_definitions():
        try:
            count = counts[definition.stat_id]
        except (KeyError, TypeError):
            raise ValueError(
                "stat_rates requires a count for every registry id; missing "
                "%r" % (definition.stat_id,)
            ) from None
        rates[definition.stat_id] = StatRate(
            definition.stat_id,
            definition.definition_version,
            count.successes,
            count.opportunities,
            count.rate(),
        )
    return rates
