"""Authoritative precision layer for chip/bb-denominated math (POKER-MATH-PRECISION-001).

Binary floats never carry an authoritative amount in this layer. Authoritative
chip amounts remain plain non-negative ints (``Chips.value``); bb (big-blind)
denominated values use :class:`decimal.Decimal` computed inside a single
module-local decimal context (``PRECISION_CONTEXT_PREC`` digits) so results
cannot depend on the process-global context. No new chip/money value type is
introduced here: the helpers reuse ``Chips`` and validate inputs with the same
strict semantics as ``poker.math.chips`` (``bool``/``float``/``str``/``Decimal``
chip inputs are rejected fail-closed).

Units:
- every ``Chips``-shaped argument is in chip units, an exact non-negative int;
- bb-denominated arguments/returns are values in big-blind units
  (``Decimal``), where ``bb`` itself is a chip-unit big-blind amount.

Rounding is always explicit via :class:`RoundingPolicy`; the default process
rounding mode (``ROUND_HALF_EVEN``) is never relied upon implicitly. The only
permitted outbound conversion to the float domain is
:func:`decimal_to_float`; building a Decimal (or Chips) from a float is
forbidden because floats must never become authoritative amounts.

Serialization for bb amounts uses exactly the keys ``schema_version``,
``value`` (a decimal string, never a JSON number) and ``unit == 'bb'``; a bb
dict is intentionally NOT parseable by ``Chips.from_dict`` so the two
authorities cannot be confused.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
from collections.abc import Sequence
from decimal import Decimal
from fractions import Fraction

from poker.domain import game
from poker.domain.money import Chips

__all__ = [
    "BB_UNIT",
    "PRECISION_CONTEXT_PREC",
    "RoundingPolicy",
    "Tolerance",
    "allocate_exact",
    "bb_from_dict",
    "bb_to_dict",
    "decimal_to_float",
    "float_within",
    "format_bb",
    "from_bb",
    "round_bb",
    "to_bb",
]

#: Number of significant digits for every Decimal computation in this module.
PRECISION_CONTEXT_PREC = 50

#: Fixed unit label for bb-denominated serialized amounts.
BB_UNIT = "bb"

_BB_KEYS = frozenset({"schema_version", "value", "unit"})

#: Module-local context: exact/high-precision DECIMAL arithmetic only.
#: Emin/Emax are widened to the full decimal range so chip-magnitude
#: conversions cannot raise boundary ``InvalidOperation``; traps stay at the
#: library defaults. Every computation below runs inside
#: ``decimal.localcontext(_CONTEXT)`` — the process-global context is never
#: read, mutated or relied upon.
_CONTEXT = decimal.Context(
    prec=PRECISION_CONTEXT_PREC,
    Emin=decimal.MIN_EMIN,
    Emax=decimal.MAX_EMAX,
)


def _as_chip_value(candidate: object, label: str) -> int:
    """Accept Chips or a plain int; reject bool/float/str/Decimal fail-closed.

    Local mirror of ``poker.math.chips._as_chip_value`` (deliberately not a
    cross-module private import); it must NOT create a new value type.
    """
    if isinstance(candidate, Chips):
        return candidate.value
    if type(candidate) is int:
        return candidate
    raise TypeError(
        f"{label} must be Chips or a plain int, got {type(candidate).__name__}"
    )


def to_bb(chips: Chips | int, bb: Chips | int) -> Decimal:
    """Return the exact big-blind ratio ``chips / bb`` as a Decimal.

    ``chips`` and ``bb`` must be ``Chips`` or plain ints (other types raise
    ``TypeError``); ``bb`` must be positive and ``chips`` non-negative
    (``ValueError`` otherwise). The division is a ``Decimal / Decimal`` division
    inside the module-local context — raw ``int / int`` true division is never
    used because it silently yields a binary float. No rounding happens inside
    ``to_bb``: non-terminating ratios carry full context precision.
    """
    chips_value = _as_chip_value(chips, "chips")
    bb_value = _as_chip_value(bb, "bb")
    if bb_value <= 0:
        raise ValueError(f"big blind must be positive, got {bb_value}")
    if chips_value < 0:
        raise ValueError(f"chip amount must be non-negative, got {chips_value}")
    with decimal.localcontext(_CONTEXT):
        return Decimal(chips_value) / Decimal(bb_value)


def from_bb(amount_bb: Decimal | str, bb: Chips | int) -> Chips:
    """Reconstruct an integer chip amount from a bb value.

    ``amount_bb`` must be a ``Decimal`` instance or a decimal ``str`` (parsed
    fail-closed); ``bool``/``int``/``float``/other types raise ``TypeError``.
    ``bb`` is validated like :func:`to_bb`. The product
    ``amount_bb * bb_value`` must be an exact non-negative integer; a lossy
    or negative reconstruction raises ``ValueError`` instead of silently
    rounding the result into a ``Chips``.
    """
    bb_value = _as_chip_value(bb, "bb")
    if bb_value <= 0:
        raise ValueError(f"big blind must be positive, got {bb_value}")
    if isinstance(amount_bb, Decimal):
        amount = amount_bb
    elif isinstance(amount_bb, str):
        try:
            amount = Decimal(amount_bb)
        except decimal.InvalidOperation as exc:
            raise ValueError(f"invalid bb amount literal: {amount_bb!r}") from exc
    else:
        raise TypeError(
            "amount_bb must be Decimal or str, got " f"{type(amount_bb).__name__}"
        )
    if not amount.is_finite():
        raise ValueError(f"bb amount must be a finite decimal, got {amount}")
    with decimal.localcontext(_CONTEXT):
        product = amount * Decimal(bb_value)
        integral = product.to_integral_value()
    if product != integral:
        raise ValueError(
            f"bb amount {amount} is lossy for bb={bb_value}; "
            "refusing to round an authoritative chip amount"
        )
    if product < 0:
        raise ValueError(f"bb amount must be non-negative, got {amount}")
    return Chips(int(integral))


class RoundingPolicy(enum.Enum):
    """Named rounding policies; never the implicit ``ROUND_HALF_EVEN``.

    ``PRESENTATION`` rounds user-facing bb displays (``.5`` away from zero,
    matching board/hud conventions); ``CONSERVATIVE_ALLOCATION`` rounds down
    so no value is ever over-credited by rounding alone.
    """

    PRESENTATION = decimal.ROUND_HALF_UP
    CONSERVATIVE_ALLOCATION = decimal.ROUND_DOWN


def round_bb(value: Decimal, places: int = 2, policy: RoundingPolicy = RoundingPolicy.PRESENTATION) -> Decimal:
    """Quantize a bb ``Decimal`` to ``places`` decimals under ``policy``.

    ``value`` must be a ``Decimal`` instance and ``places`` a plain non-negative
    int (>= 0). Quantization runs inside the module-local context where
    ``prec=50`` keeps even 2#63-scale amounts within range (no
    ``InvalidOperation``). The rounding mode is always taken explicitly from
    ``policy``; ``ROUND_HALF_EVEN`` is never applied implicitly.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"value must be Decimal, got {type(value).__name__}")
    if type(places) is not int:
        raise TypeError(f"places must be a plain int, got {type(places).__name__}")
    if places < 0:
        raise ValueError(f"places must be >= 0, got {places}")
    if not isinstance(policy, RoundingPolicy):
        raise TypeError(f"policy must be a RoundingPolicy, got {type(policy).__name__}")
    with decimal.localcontext(_CONTEXT):
        exponent = Decimal(1).scaleb(-places)
        return value.quantize(exponent, rounding=policy.value)


def format_bb(chips: Chips | int, bb: Chips | int, places: int = 2) -> str:
    """Format ``chips`` expressed in bb units as a fixed-places string.

    Presentation only: :func:`to_bb` followed by :func:`round_bb` under
    :attr:`RoundingPolicy.PRESENTATION`, then ``str`` of the quantized Decimal
    (never a float, never back into arithmetic — rebuild exact amounts with
    :func:`from_bb` on a Decimal instead).
    """
    return str(round_bb(to_bb(chips, bb), places, RoundingPolicy.PRESENTATION))


def allocate_exact(weights: Sequence[Chips | int], total: Chips | int) -> list[Chips]:
    """Partition ``total`` chips across ``weights`` with exact chip conservation.

    Each share is the exact rational ``share_i = total * w_i / W`` (with
    ``W = sum(weights)``); shares are floored to whole chips and the leftover
    ``total - sum(floors)`` is distributed one chip at a time to the shares with
    the largest fractional remainders, deterministically tie-broken by lowest
    index first. Sum of the returned parts equals ``total`` exactly for every
    allowed input (chip conservation identity); rounded parts are never re-summed
    and re-rounded.

    Validation: weights and ``total`` use the strict Chips-value semantics
    (Chips or plain int; ``bool``/``float``/``str``/``Decimal`` raise
    ``TypeError``) and must be non-negative (``ValueError`` otherwise).
    ``total > 0`` with all-zero weights is undefined and raises ``ValueError``;
    ``total == 0`` yields all ``Chips(0)`` regardless of weights.
    """
    weight_values = [_as_chip_value(weight, "weight") for weight in weights]
    total_value = _as_chip_value(total, "total")
    for index, weight in enumerate(weight_values):
        if weight < 0:
            raise ValueError(f"weight at index {index} must be non-negative, got {weight}")
    if total_value < 0:
        raise ValueError(f"total must be non-negative, got {total_value}")
    if total_value == 0:
        return [Chips(0) for _ in weight_values]
    weight_sum = sum(weight_values)
    if weight_sum == 0:
        raise ValueError("cannot allocate a positive total across all-zero weights")
    floors: list[int] = []
    remainders: list[Fraction] = []
    for weight in weight_values:
        share = Fraction(total_value * weight, weight_sum)
        floor = share.numerator // share.denominator
        floors.append(floor)
        remainders.append(share - floor)
    leftover = total_value - sum(floors)
    order = sorted(range(len(weight_values)), key=lambda index: (-remainders[index], index))
    for index in order[:leftover]:
        floors[index] += 1
    return [Chips(value) for value in floors]


def bb_to_dict(value: Decimal) -> dict:
    """Serialize a bb amount: exactly ``{'schema_version', 'value', 'unit'}``.

    ``value`` is serialized as ``str(Decimal)`` at full precision (never a JSON
    number/float) and ``unit`` is always ``'bb'``. Non-finite decimals (NaN,
    Infinity) fail closed. This is a separate authority from
    ``Chips.to_dict``: the produced dict is NOT parseable by
    ``Chips.from_dict``.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"bb value must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"bb value must be a finite decimal, got {value}")
    return {
        "schema_version": game.SCHEMA_VERSION,
        "value": str(value),
        "unit": BB_UNIT,
    }


def bb_from_dict(data: dict) -> Decimal:
    """Rebuild a bb ``Decimal`` from its serialized form (fail-closed).

    Requires exactly the keys ``schema_version``, ``value`` and ``unit`` with
    ``unit == 'bb'``; the ``value`` must already be a decimal ``str``
    (``int``/``float`` are rejected because a bb amount never travels as a JSON
    number). Malformed and non-finite literals (``NaN``, ``Infinity``,
    ``-Infinity``), wrong units, missing/extra keys and foreign schema
    versions all fail closed.
    """
    game.require_schema_version(data)
    game.require_exact_keys(data, _BB_KEYS, "bb amount")
    if data["unit"] != BB_UNIT:
        raise ValueError(f"unsupported unit: {data['unit']!r}")
    raw = data["value"]
    if not isinstance(raw, str):
        raise TypeError(f"bb amount value must be a decimal str, got {type(raw).__name__}")
    try:
        value = Decimal(raw)
    except decimal.InvalidOperation as exc:
        raise ValueError(f"invalid bb amount value: {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"bb amount value must be a finite decimal, got {raw!r}")
    return value


@dataclasses.dataclass(frozen=True)
class Tolerance:
    """Explicit absolute/relative tolerance pair for OUTBOUND float comparisons.

    Both fields are mandatory (no defaults) and must be exactly ``Decimal``
    instances that are finite and non-negative; ``bool``/``int``/``float``/
    ``str`` are rejected. Exact-domain comparisons (int/int, Decimal/Decimal)
    are exact and must never go through a tolerance.
    """

    abs_tol: Decimal
    rel_tol: Decimal

    def __post_init__(self) -> None:
        for name in ("abs_tol", "rel_tol"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(
                    f"{name} must be exactly Decimal, got " f"{type(value).__name__}"
                )
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite non-negative Decimal")


def decimal_to_float(value: Decimal) -> float:
    """The ONE documented one-way outbound conversion into the float domain.

    Used only to leave the exact domain at a genuine float boundary (for
    example float-only external libraries or plots). Rebuilding a Decimal or a
    Chips from a float is forbidden: binary floats never become authoritative
    amounts.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"value must be Decimal, got {type(value).__name__}")
    return float(value)


def float_within(a: float, b: float, tol: Tolerance) -> bool:
    """Float-domain only comparison ``|a - b| <= abs_tol + rel_tol * |b|``.

    ``tol.abs_tol`` and ``tol.rel_tol`` cross back into the float domain through
    :func:`decimal_to_float` (the single documented outbound conversion).
    ``b`` is the reference/oracle side of the comparison. Authoritative
    int/Decimal comparisons are exact and must never use this helper.
    """
    if not isinstance(a, float):
        raise TypeError(f"a must be a float, got {type(a).__name__}")
    if not isinstance(b, float):
        raise TypeError(f"b must be a float, got {type(b).__name__}")
    if not isinstance(tol, Tolerance):
        raise TypeError(f"tol must be Tolerance, got {type(tol).__name__}")
    return abs(a - b) <= decimal_to_float(tol.abs_tol) + decimal_to_float(tol.rel_tol) * abs(b)
