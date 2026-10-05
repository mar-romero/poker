"""Pure integer chip-arithmetic helpers (scope-authorized for POKER-CORE-POTS-001).

All operations go through ``Chips.value`` (plain non-negative ints); no
operator overloads are added to ``Chips`` itself and no new chip type is
introduced. Every helper returns ``Chips`` so results stay inside the
authoritative value domain; floats, bools and strings are rejected.
"""

from __future__ import annotations

from collections.abc import Iterable

from poker.domain.money import Chips

__all__ = ["add", "clamp", "sum_all"]


def _as_chip_value(candidate: object, label: str) -> int:
    """Accept Chips or a plain int; reject bool/float/str/Decimal fail-closed."""
    if isinstance(candidate, Chips):
        return candidate.value
    if type(candidate) is int:
        return candidate
    raise TypeError(
        f"{label} must be Chips or a plain int, got {type(candidate).__name__}"
    )


def add(a: Chips | int, b: Chips | int) -> Chips:
    """Return the exact sum of two chip amounts as ``Chips``."""
    return Chips(_as_chip_value(a, "a") + _as_chip_value(b, "b"))


def sum_all(amounts: Iterable[Chips | int]) -> Chips:
    """Return the sum of an iterable of chip amounts; empty input is Chips(0)."""
    total = 0
    for amount in amounts:
        total += _as_chip_value(amount, "amount")
    return Chips(total)


def clamp(x: Chips | int, lo: Chips | int, hi: Chips | int) -> Chips:
    """Clamp ``x`` into ``[lo, hi]``; a negative effective value floors at 0."""
    value = _as_chip_value(x, "x")
    low = _as_chip_value(lo, "lo")
    high = _as_chip_value(hi, "hi")
    if value < low:
        value = low
    if value > high:
        value = high
    if value < 0:
        value = 0
    return Chips(value)
