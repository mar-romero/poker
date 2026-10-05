"""Tiny pure-arithmetic package for chip math.

Scope-authorized addition (planner S2 / orchestrator scope-expansion):
re-exports the chip helpers plus ``Chips``; contains no other logic, no
operator overloads and no new chip type.
"""

from poker.domain.money import Chips
from poker.math.chips import add, clamp, sum_all

__all__ = ["Chips", "add", "clamp", "sum_all"]
