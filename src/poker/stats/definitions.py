"""Versioned statistic definitions for the opportunity evaluator.

Task POKER-STATS-OPPORTUNITY-001 (planner D2/D6/D8 pins; risk R2, TDD).
This module owns the closed registry of the six pinned opportunity
definitions; :mod:`poker.stats.opportunity` evaluates them PURELY over
``StateEngine.replay`` artifacts. No ORM/database imports and no IO.

Version governance (D6, mirroring docs/specs/STATISTICS_CATALOG.md):

* ``DEFINITIONS_VERSION = '1.0.0'``; every StatDefinition carries
  ``definition_version``. ANY predicate, success, dimension or exclusion
  change creates a NEW version constant — never a silent edit — because
  historical aggregates produced by incompatible definitions are never
  merged (catalog definition-governance rule).
* ``OpportunityMatch`` / ``StatCount.rate()`` (None on zero opportunities)
  and ``definitions_to_dict()`` are the machine-readable downstream contract
  consumed by POKER-STATS-PREFLOP/POSTFLOP/DSL/BAYES; do not change them
  without a ``DEFINITIONS_VERSION`` bump.

Pinned predicate semantics (planner D2; notation: a decision point is a
``DecisionSnapshot`` with ``actor_to_act != None``; the actor's DECIDED
action is ``actions[snapshot.action_index + 1]``; raise/call counts are
same-street counts strictly before the decision):

* ``three_bet``: preflop, raise_count == 1 AND call_count == 0 (raise over a
  lone open; raise-over-limpers is an iso-raise, excluded); success = RAISE.
* ``fold_to_three_bet``: same opportunity conditions; success = FOLD; the
  actor is never the opener (under strict replay the opener is marked acted;
  the correspondence fails closed).
* ``cbet``: flop, actor == preflop final aggressor (actor of the LAST
  preflop BET/RAISE), flop raise_count == 0; success = BET. No preflop raise
  => no c-bet opportunities (limped pots excluded).
* ``fold_to_cbet``: flop, flop raise_count == 1 AND that flop bettor ==
  preflop final aggressor (a TRUE c-bet; folds vs donk/probe bets are
  excluded), actor != bettor; success = FOLD. ``opener_position`` is keyed
  to the preflop final aggressor's position (test-designer F10 pin; an
  extension of the D2 dimensions list, documented in the definition).
* ``steal``: preflop, raise_count == 0 AND call_count == 0 (unopened), actor
  position in {CO, BTN, SB}; success = RAISE (an SB complete/CALL is NOT a
  steal success). Documented tracker-convention divergences: SB opens count
  as steal attempts, and heads-up the BTN(=SB) open vs BB counts too — the
  ``heads_up`` dimension distinguishes them.
* ``bb_defense``: preflop, actor position == 'BB', raise_count == 1 (a
  single open from any opener position; limped callers are allowed per the
  catalog "BB defense vs each position"); success = CALL or RAISE
  (defended). A FOLD is not a defense.

Exclusion rule D3 (referenced by every definition's ``exclusions`` text):
an opportunity exists only if the actor is present and active, is not
already all-in (``effective_stack[actor] > 0``), and the legal_actions
contain at least one kind other than FOLD; facing-all-in ``{FOLD, CALL}``
states REMAIN opportunities; terminal snapshots (actor None) never match.
"""

from __future__ import annotations

import dataclasses

from poker.domain.game import Street

__all__ = [
    "DEFINITIONS_VERSION",
    "StatDefinition",
    "definitions_to_dict",
    "get_definition",
    "list_definitions",
]

DEFINITIONS_VERSION = "1.0.0"
"""Definition-set version ('1.0.0'); bump on any semantics change (D6)."""

_EXCLUSIONS_D3 = (
    "D3 all-in/no-decision exclusions: the actor must be present "
    "(actor_to_act not None) and among active_players, must not already be "
    "all-in (effective_stack[actor] > 0), and the legal_actions must contain "
    "at least one kind other than FOLD; facing-all-in {FOLD, CALL} decisions "
    "REMAIN opportunities (fold-vs-shove is a genuine decision); terminal "
    "snapshots (actor None) never produce matches."
)

@dataclasses.dataclass(frozen=True)
class StatDefinition:
    """One pinned statistic: predicate + success + dimensions + version."""

    stat_id: str
    definition_version: str
    description: str
    streets: frozenset
    predicate_spec: dict
    success_spec: dict
    dimensions: tuple
    exclusions: str

    def to_dict(self):
        """Byte-stable introspectable payload (sorted street names)."""
        return {
            "stat_id": self.stat_id,
            "definition_version": self.definition_version,
            "description": self.description,
            "streets": sorted(street.name for street in self.streets),
            "predicate_spec": dict(self.predicate_spec),
            "success_spec": dict(self.success_spec),
            "dimensions": list(self.dimensions),
            "exclusions": self.exclusions,
        }


_THREE_BET_PREDICATE = {
    "street": "PREFLOP",
    "same_street_raise_count_before": 1,
    "same_street_call_count_before": 0,
}

_STEAL_DEF = StatDefinition(
    stat_id="steal",
    definition_version=DEFINITIONS_VERSION,
    description=(
        "Steal attempt: an unopened preflop raise attempt from CO, BTN or SB "
        "(raise_count == 0 and call_count == 0 before the decision). "
        "Documented tracker-convention divergences pinned by planner D2: an "
        "SB open counts as a steal attempt, and heads-up the BTN(=SB) open "
        "vs BB counts too; the heads_up dimension distinguishes both. "
        "Success = decided RAISE only (an SB complete/CALL is not a steal "
        "success)."
    ),
    streets=frozenset({Street.PREFLOP}),
    predicate_spec={
        "street": "PREFLOP",
        "same_street_raise_count_before": 0,
        "same_street_call_count_before": 0,
        "position_in": ["CO", "BTN", "SB"],
    },
    success_spec={"decided_action": "RAISE"},
    dimensions=("position", "heads_up"),
    exclusions=_EXCLUSIONS_D3,
)

_BB_DEFENSE_DEF = StatDefinition(
    stat_id="bb_defense",
    definition_version=DEFINITIONS_VERSION,
    description=(
        "BB defense: the big blind facing a SINGLE preflop open "
        "(raise_count == 1, any opener position; limped callers are allowed "
        "per the catalog 'BB defense vs each position'). Success = decided "
        "CALL or RAISE (defended); a FOLD is not a defense. Facing a second "
        "raise (raise_count >= 2) is not a single-open defense opportunity."
    ),
    streets=frozenset({Street.PREFLOP}),
    predicate_spec={
        "street": "PREFLOP",
        "same_street_raise_count_before": 1,
        "actor_position": "BB",
    },
    success_spec={"decided_action": ["CALL", "RAISE"]},
    dimensions=("opener_position", "heads_up"),
    exclusions=_EXCLUSIONS_D3,
)

_THREE_BET_DEF = StatDefinition(
    stat_id="three_bet",
    definition_version=DEFINITIONS_VERSION,
    description=(
        "Three-bet: a preflop raise over a LONE open (raise_count == 1 AND "
        "call_count == 0 before the decision; a raise over limpers is an "
        "iso-raise and is excluded by the call_count == 0 condition). The "
        "opener's own opening decision is not a three-bet opportunity (the "
        "opener is marked acted under strict replay). Success = decided "
        "RAISE."
    ),
    streets=frozenset({Street.PREFLOP}),
    predicate_spec=dict(_THREE_BET_PREDICATE),
    success_spec={"decided_action": "RAISE"},
    dimensions=("position", "heads_up"),
    exclusions=_EXCLUSIONS_D3,
)

_FOLD_TO_THREE_BET_DEF = StatDefinition(
    stat_id="fold_to_three_bet",
    definition_version=DEFINITIONS_VERSION,
    description=(
        "Fold to three-bet: a FOLD facing a lone preflop open (same "
        "opportunity conditions as three_bet: raise_count == 1 AND "
        "call_count == 0; re-opened decisions after the 3-bet carry "
        "raise_count >= 2 and match nothing). opener_position is the "
        "position of the lone preflop raiser (the opener). Success = "
        "decided FOLD."
    ),
    streets=frozenset({Street.PREFLOP}),
    predicate_spec=dict(_THREE_BET_PREDICATE),
    success_spec={"decided_action": "FOLD"},
    dimensions=("position", "opener_position", "heads_up"),
    exclusions=_EXCLUSIONS_D3,
)

_CBET_DEF = StatDefinition(
    stat_id="cbet",
    definition_version=DEFINITIONS_VERSION,
    description=(
        "Continuation bet: a flop BET by the preflop final aggressor (the "
        "actor of the LAST preflop BET/RAISE) with no flop aggression before "
        "the decision (flop raise_count == 0). A hand without a preflop "
        "raise has no c-bet opportunities (limped pots excluded); a flop bet "
        "by any other player is a donk/probe, not a c-bet. Success = decided "
        "BET."
    ),
    streets=frozenset({Street.FLOP}),
    predicate_spec={
        "street": "FLOP",
        "same_street_raise_count_before": 0,
        "actor_is": "preflop_final_aggressor",
    },
    success_spec={"decided_action": "BET"},
    dimensions=("position", "heads_up"),
    exclusions=_EXCLUSIONS_D3,
)

_FOLD_TO_CBET_DEF = StatDefinition(
    stat_id="fold_to_cbet",
    definition_version=DEFINITIONS_VERSION,
    description=(
        "Fold to continuation bet: a FOLD facing a TRUE c-bet — the flop "
        "raise_count is exactly 1 and that flop bettor IS the preflop final "
        "aggressor, and the actor is not the bettor. Folds vs donk/probe "
        "bets (flop bettor != preflop final aggressor) are excluded. "
        "opener_position is keyed to the preflop final aggressor's position "
        "(test-designer F10 pin; an extension of the planner D2 dimensions "
        "list, documented here). Success = decided FOLD."
    ),
    streets=frozenset({Street.FLOP}),
    predicate_spec={
        "street": "FLOP",
        "same_street_raise_count_before": 1,
        "street_bettor_is": "preflop_final_aggressor",
        "actor_is_not": "street_bettor",
    },
    success_spec={"decided_action": "FOLD"},
    dimensions=("position", "opener_position", "heads_up"),
    exclusions=_EXCLUSIONS_D3,
)

_DEFINITIONS = (
    _THREE_BET_DEF,
    _FOLD_TO_THREE_BET_DEF,
    _CBET_DEF,
    _FOLD_TO_CBET_DEF,
    _STEAL_DEF,
    _BB_DEFENSE_DEF,
)

_DEFINITIONS_BY_ID = {definition.stat_id: definition for definition in _DEFINITIONS}


def list_definitions():
    """All six pinned definitions in fixed registry order (deterministic)."""
    return tuple(_DEFINITIONS)


def get_definition(stat_id):
    """One definition by stat id; unknown ids fail closed (ValueError)."""
    try:
        return _DEFINITIONS_BY_ID[stat_id]
    except (KeyError, TypeError):
        raise ValueError("unknown statistic id: %r" % (stat_id,)) from None


def definitions_to_dict():
    """Introspectable {stat_id: payload} registry (machine-readable D8)."""
    return {definition.stat_id: definition.to_dict() for definition in _DEFINITIONS}
