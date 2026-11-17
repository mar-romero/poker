"""Versioned opportunity-based statistics over replayed poker hands.

Public API (task POKER-STATS-OPPORTUNITY-001, planner D8 pin):

* ``evaluate_hand(table, blinds, actions, *, button_seat) -> HandEvaluation``
  — pure replay-based opportunity evaluation.
* ``OpportunityMatch`` / ``StatCount`` / ``HandEvaluation`` — frozen result
  dataclasses (successes = matches with ``succeeded``; opportunities = all
  matches; zero opportunities => zero matches and ``rate() is None``).
* ``DEFINITIONS_VERSION``, ``StatDefinition``, ``list_definitions``,
  ``get_definition``, ``definitions_to_dict`` — versioned, introspectable
  definition registry (AC3).
* ``is_decision_opportunity`` / ``is_heads_up`` / ``decided_action`` — pure
  helpers pinning the D3 exclusion rule, the D4 heads-up rule and the U1
  correspondence check.

No sqlalchemy, no IO, no wall-clock, no randomness: evaluation is PURE over
``StateEngine.replay`` artifacts (planner D1).
"""

from .definitions import (
    DEFINITIONS_VERSION,
    StatDefinition,
    definitions_to_dict,
    get_definition,
    list_definitions,
)
from .opportunity import (
    HandEvaluation,
    OpportunityMatch,
    StatCount,
    decided_action,
    evaluate_hand,
    is_decision_opportunity,
    is_heads_up,
)

__all__ = [
    "DEFINITIONS_VERSION",
    "StatDefinition",
    "definitions_to_dict",
    "get_definition",
    "list_definitions",
    "HandEvaluation",
    "OpportunityMatch",
    "StatCount",
    "decided_action",
    "evaluate_hand",
    "is_decision_opportunity",
    "is_heads_up",
]
