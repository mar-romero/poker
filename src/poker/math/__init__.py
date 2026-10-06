"""Tiny pure-arithmetic package for chip math.

Scope-authorized addition (planner S2 / orchestrator scope-expansion):
re-exports the chip helpers plus ``Chips``; contains no other logic, no
operator overloads and no new chip type.

Scope-authorized addition (POKER-MATH-PRECISION-001): re-exports the
authoritative precision layer for bb-denominated presentation, named rounding
policies and explicit tolerances; still no logic beyond the re-exports and no
new chip/money value type.
"""

from poker.domain.money import Chips
from poker.math.chips import add, clamp, sum_all
from poker.math.precision import (
    BB_UNIT,
    PRECISION_CONTEXT_PREC,
    RoundingPolicy,
    Tolerance,
    allocate_exact,
    bb_from_dict,
    bb_to_dict,
    decimal_to_float,
    float_within,
    format_bb,
    from_bb,
    round_bb,
    to_bb,
)

__all__ = [
    "BB_UNIT",
    "Chips",
    "PRECISION_CONTEXT_PREC",
    "RoundingPolicy",
    "Tolerance",
    "add",
    "allocate_exact",
    "bb_from_dict",
    "bb_to_dict",
    "clamp",
    "decimal_to_float",
    "float_within",
    "format_bb",
    "from_bb",
    "round_bb",
    "sum_all",
    "to_bb",
]
