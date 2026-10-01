"""Authoritative chip amounts.

Chip/money values use integer chip units only: binary floats never carry an
authoritative amount. Serialized amount nodes carry exactly the keys
``schema_version``, ``value`` (a plain non-negative int, never a bool) and
``unit`` (always ``'chips'``).
"""

from __future__ import annotations

import dataclasses

__all__ = ["CHIPS_UNIT", "Chips"]

CHIPS_UNIT = "chips"

_CHIPS_KEYS = frozenset({"schema_version", "value", "unit"})


@dataclasses.dataclass(frozen=True)
class Chips:
    """A non-negative integer chip amount (frozen value type)."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError(
                "Chips value must be a plain int; got "
                f"{type(self.value).__name__} (bool, float, str, Decimal are rejected)"
            )
        if self.value < 0:
            raise ValueError("Chips value must be non-negative")

    def to_dict(self) -> dict:
        from poker.domain import game  # single source of SCHEMA_VERSION

        return {
            "schema_version": game.SCHEMA_VERSION,
            "value": self.value,
            "unit": CHIPS_UNIT,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Chips":
        from poker.domain import game

        game.require_schema_version(payload)
        game.require_exact_keys(payload, _CHIPS_KEYS, "Chips")
        if payload["unit"] != CHIPS_UNIT:
            raise ValueError(f"unsupported unit: {payload['unit']!r}")
        return cls(payload["value"])
