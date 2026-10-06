"""Precision-layer tests for POKER-MATH-PRECISION-001.

Covers exact Decimal bb conversion, strictly-validated bb reconstruction,
named rounding policies with largest-remainder chip allocation, bb-denominated
serialization separate from Chips authority, explicit Tolerance objects, and a
meta-test asserting precision.py never touches binary floats in authoritative
paths. Validation errors are asserted as ``(ValueError, TypeError)`` where the
exact exception class is an implementation freedom, mirroring
``tests/domain/test_game_model.py``.
"""

from __future__ import annotations

import ast
import decimal
import json
import unittest
from decimal import Decimal
from pathlib import Path

import poker.math
from poker.domain.game import BlindAnteConfig
from poker.domain.money import Chips
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

VALIDATION_ERRORS = (ValueError, TypeError)

# Independent context for test oracles (never importing precision internals).
_ORACLE_CONTEXT = decimal.Context(prec=PRECISION_CONTEXT_PREC)


def _oracle_div(numerator: int, denominator: int) -> Decimal:
    """Independent exact Decimal division computed in a prec>=50 context."""
    with decimal.localcontext(_ORACLE_CONTEXT):
        return Decimal(numerator) / Decimal(denominator)


class TestToBB(unittest.TestCase):
    """Exact hand oracles and fail-closed validation for ``to_bb``."""

    def test_exact_ratio_2500_over_100(self) -> None:
        self.assertEqual(to_bb(2500, 100), Decimal(25))
        self.assertIsInstance(to_bb(2500, 100), Decimal)
        self.assertNotIsInstance(to_bb(2500, 100), float)

    def test_exact_ratio_fractional_result(self) -> None:
        self.assertEqual(to_bb(12345, 100), Decimal("123.45"))

    def test_zero_chips(self) -> None:
        self.assertEqual(to_bb(0, 100), Decimal(0))
        self.assertEqual(format_bb(0, 100), "0.00")

    def test_format_six_places(self) -> None:
        self.assertEqual(format_bb(2500, 100, places=6), "25.000000")

    def test_non_terminating_ratio_exact_against_oracle(self) -> None:
        value = to_bb(1, 3)
        self.assertIsInstance(value, Decimal)
        self.assertNotIsInstance(value, float)
        self.assertEqual(value, _oracle_div(1, 3))
        # Full context precision: no early rounding to fewer digits.
        with decimal.localcontext(_ORACLE_CONTEXT):
            self.assertEqual(len(value.as_tuple().digits), PRECISION_CONTEXT_PREC)

    def test_precision_context_is_normative_50(self) -> None:
        # Pin the spec's normative precision literally: the oracle context is
        # built with the literal 50 (never derived from the module constant).
        oracle = decimal.Context(prec=50)
        with decimal.localcontext(oracle):
            self.assertEqual(Decimal(1) / Decimal(3), to_bb(1, 3))
        import poker.math.precision as precision_module

        self.assertEqual(precision_module.PRECISION_CONTEXT_PREC, 50)

    def test_bb_zero_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(100, 0)

    def test_bb_negative_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(100, -5)

    def test_negative_chips_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(-1, 100)

    def test_bool_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(True, 100)
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(100, False)

    def test_float_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(10.5, 100)
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(100, 2.0)

    def test_str_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb("10", 100)
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(100, "2")

    def test_decimal_chip_input_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(Decimal(10), 100)
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(100, Decimal(2))

    def test_chips_instances_accepted(self) -> None:
        self.assertEqual(to_bb(Chips(2500), Chips(100)), Decimal(25))


class TestFromBB(unittest.TestCase):
    """Exact reconstruction and lossy-refusal behavior of ``from_bb``."""

    def test_round_trip_from_to_bb(self) -> None:
        self.assertEqual(from_bb(to_bb(2500, 100), 100), Chips(2500))

    def test_str_amount(self) -> None:
        self.assertEqual(from_bb("25", 100), Chips(2500))

    def test_lossy_ratio_refused(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(to_bb(1, 3), 3)

    def test_float_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(25.0, 100)

    def test_int_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(25, 100)

    def test_bool_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(True, 100)

    def test_malformed_str_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb("twenty-five", 100)

    def test_bb_zero_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb("25", 0)

    def test_bb_negative_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb("25", -1)

    def test_negative_amount_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(Decimal("-1"), 100)


class TestStakesRoundTrip(unittest.TestCase):
    """BlindAnteConfig fixtures round-trip and stake-consistent bb math."""

    STAKES = (
        (5, 10, 0),
        (1, 2, 1),
        (10, 10, 25),
    )

    def test_json_round_trip_equality(self) -> None:
        for sb, bb, ante in self.STAKES:
            with self.subTest(sb=sb, bb=bb, ante=ante):
                original = BlindAnteConfig(sb, bb, ante)
                payload = original.to_dict()
                canonical = json.dumps(payload, sort_keys=True)
                restored = BlindAnteConfig.from_dict(json.loads(canonical))
                self.assertEqual(restored, original)

    def test_bb_math_round_trip_per_stake(self) -> None:
        for sb, bb, ante in self.STAKES:
            with self.subTest(sb=sb, bb=bb, ante=ante):
                bb_chips = Chips(bb)
                amount = 500 + sb  # divisible by every fixture bb
                self.assertEqual(
                    from_bb(to_bb(from_bb(to_bb(amount, bb_chips), bb_chips), bb_chips), bb_chips),
                    Chips(amount),
                )

    def test_all_zero_stakes_valid_and_bb_zero_raises(self) -> None:
        original = BlindAnteConfig(0, 0, 0)
        self.assertEqual(BlindAnteConfig.from_dict(original.to_dict()), original)
        with self.assertRaises(VALIDATION_ERRORS):
            to_bb(100, 0)


class TestRoundingAndAllocation(unittest.TestCase):
    """Named rounding policies and largest-remainder chip conservation."""

    def test_half_up_presentation_rounds_up(self) -> None:
        self.assertEqual(round_bb(Decimal("0.5"), 0), Decimal(1))
        self.assertEqual(round_bb(Decimal("2.5"), 0), Decimal(3))

    def test_round_down_conservative(self) -> None:
        self.assertEqual(
            round_bb(Decimal("0.5"), 0, RoundingPolicy.CONSERVATIVE_ALLOCATION),
            Decimal(0),
        )

    def test_round_bb_type_and_domain_validation(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            round_bb("0.5", 0)
        with self.assertRaises(VALIDATION_ERRORS):
            round_bb(Decimal("0.5"), -1)
        with self.assertRaises(VALIDATION_ERRORS):
            # Plain string as policy: must be a RoundingPolicy instance.
            round_bb(Decimal("0.5"), 0, "presentation")  # type: ignore[arg-type]
        with self.assertRaises(VALIDATION_ERRORS):
            # Raw rounding constant is not a RoundingPolicy member either.
            round_bb(Decimal("0.5"), 0, decimal.ROUND_HALF_UP)  # type: ignore[arg-type]

    def test_allocate_equal_weights_largest_remainder(self) -> None:
        parts = allocate_exact([1, 1, 1], 100)
        self.assertEqual(parts, [Chips(34), Chips(33), Chips(33)])
        self.assertEqual(sum(part.value for part in parts), 100)

    def test_allocate_unequal_weights_deterministic(self) -> None:
        parts = allocate_exact([1, 2], 100)
        # floors 33/66; leftover 1 chip goes to the larger remainder (0.666…).
        self.assertEqual(parts, [Chips(33), Chips(67)])
        self.assertEqual(sum(part.value for part in parts), 100)

    def test_allocate_zero_total(self) -> None:
        self.assertEqual(allocate_exact([1, 1], 0), [Chips(0), Chips(0)])

    def test_allocate_positive_total_zero_weights_raises(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            allocate_exact([0, 0], 100)

    def test_allocate_negative_weight_raises(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            allocate_exact([-1, 2], 100)

    def test_allocate_invalid_weight_type_raises(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            allocate_exact([1.5, 2], 100)

    def test_allocate_conservation_identity(self) -> None:
        cases = [
            ([3, 5, 7], 1000),
            ([1, 1, 1, 1, 1], 13),
            ([7], 250),
            ([2, 3, 5, 7, 11], 97),
            ([11, 7, 5, 3, 2], 998),
        ]
        for weights, total in cases:
            with self.subTest(weights=weights, total=total):
                parts = allocate_exact(weights, total)
                self.assertEqual(sum(part.value for part in parts), total)
                self.assertEqual(len(parts), len(weights))

    def test_allocate_tie_break_prefers_lowest_index(self) -> None:
        # [1,1] total 5: shares 2.5/2.5 -> floors 2,2 (sum 4), leftover 1;
        # remainders tie at .5 -> the chip goes to the lowest index.
        parts = allocate_exact([1, 1], 5)
        self.assertEqual(parts, [Chips(3), Chips(2)])
        self.assertEqual(sum(part.value for part in parts), 5)
        # [1,1,1,2] total 11: shares 2.2,2.2,2.2,4.4 -> floors 2,2,2,4,
        # leftover 1 -> largest remainder .4 at index 3 wins (no tie).
        self.assertEqual(allocate_exact([1, 1, 1, 2], 11), [Chips(2), Chips(2), Chips(2), Chips(5)])


class TestSerialization(unittest.TestCase):
    """bb dicts are their own authority, disjoint from Chips serialization."""

    def test_bb_to_dict_exact_shape(self) -> None:
        payload = bb_to_dict(Decimal(25))
        self.assertEqual(set(payload), {"schema_version", "value", "unit"})
        self.assertEqual(payload["value"], "25")
        self.assertIsInstance(payload["value"], str)
        self.assertEqual(payload["unit"], BB_UNIT)

    def test_round_trip_preserves_decimal_equality(self) -> None:
        value = Decimal("123.45")
        self.assertEqual(bb_from_dict(bb_to_dict(value)), value)

    def test_full_precision_str_value(self) -> None:
        value = _oracle_div(1, 3)
        restored = bb_from_dict(bb_to_dict(value))
        self.assertEqual(restored, value)

    def test_int_value_rejected(self) -> None:
        payload = bb_to_dict(Decimal(25))
        payload["value"] = 25
        with self.assertRaises(VALIDATION_ERRORS):
            bb_from_dict(payload)

    def test_float_value_rejected(self) -> None:
        payload = bb_to_dict(Decimal(25))
        payload["value"] = 25.5
        with self.assertRaises(VALIDATION_ERRORS):
            bb_from_dict(payload)

    def test_missing_key_rejected(self) -> None:
        payload = bb_to_dict(Decimal(25))
        del payload["unit"]
        with self.assertRaises(VALIDATION_ERRORS):
            bb_from_dict(payload)

    def test_extra_key_rejected(self) -> None:
        payload = bb_to_dict(Decimal(25))
        payload["extra"] = 1
        with self.assertRaises(VALIDATION_ERRORS):
            bb_from_dict(payload)

    def test_wrong_unit_rejected(self) -> None:
        payload = bb_to_dict(Decimal(25))
        payload["unit"] = "chips"
        with self.assertRaises(VALIDATION_ERRORS):
            bb_from_dict(payload)

    def test_malformed_str_value_rejected(self) -> None:
        payload = bb_to_dict(Decimal(25))
        payload["value"] = "not-a-decimal"
        with self.assertRaises(VALIDATION_ERRORS):
            bb_from_dict(payload)

    def test_non_finite_str_values_rejected(self) -> None:
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                payload = bb_to_dict(Decimal(25))
                payload["value"] = literal
                with self.assertRaises(VALIDATION_ERRORS):
                    bb_from_dict(payload)

    def test_non_finite_amount_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(Decimal("NaN"), 100)
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(Decimal("Infinity"), 100)
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb(Decimal("-Infinity"), 100)
        with self.assertRaises(VALIDATION_ERRORS):
            from_bb("NaN", 100)

    def test_bb_to_dict_non_finite_rejected(self) -> None:
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                with self.assertRaises(VALIDATION_ERRORS):
                    bb_to_dict(Decimal(literal))

    def test_bb_dict_not_parseable_by_chips_authority(self) -> None:
        payload = bb_to_dict(Decimal(25))
        payload["value"] = 25  # Chips wants an int; prove authority separation
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict(payload)
        with self.assertRaises(VALIDATION_ERRORS):
            Chips.from_dict(bb_to_dict(Decimal(25)))

    def test_chips_serialization_regression(self) -> None:
        chips = Chips(2500)
        self.assertEqual(Chips.from_dict(chips.to_dict()), chips)


class TestTolerance(unittest.TestCase):
    """Strict Tolerance construction and outbound float comparison policy."""

    def test_accepts_two_decimals(self) -> None:
        tolerance = Tolerance(Decimal("0.1"), Decimal("0.05"))
        self.assertEqual(tolerance.abs_tol, Decimal("0.1"))
        self.assertEqual(tolerance.rel_tol, Decimal("0.05"))

    def test_requires_both_fields(self) -> None:
        with self.assertRaises(TypeError):
            Tolerance(Decimal("0.1"))  # type: ignore[call-arg]

    def test_str_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance("0.1", Decimal("0.05"))  # type: ignore[arg-type]
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance(Decimal("0.1"), "0.05")  # type: ignore[arg-type]

    def test_float_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance(0.1, Decimal("0.05"))  # type: ignore[arg-type]
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance(Decimal("0.1"), 0.05)  # type: ignore[arg-type]

    def test_int_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance(1, Decimal("0.05"))  # type: ignore[arg-type]
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance(Decimal("0.1"), 0)  # type: ignore[arg-type]

    def test_negative_rejected(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance(Decimal("-0.1"), Decimal("0.05"))
        with self.assertRaises(VALIDATION_ERRORS):
            Tolerance(Decimal("0.1"), Decimal("-0.05"))

    def test_float_within_hand_computed_boundaries(self) -> None:
        tolerance = Tolerance(Decimal("0.1"), Decimal("0.05"))
        # |0.0 - 0.0| = 0 <= 0.1 + 0 = 0.1 -> inside.
        self.assertTrue(float_within(0.0, 0.0, tolerance))
        # |0.1 - 0.0| = 0.1 <= 0.1 + 0 = 0.1 -> boundary inside.
        self.assertTrue(float_within(0.1, 0.0, tolerance))
        # |0.2 - 0.0| = 0.2 > 0.1 -> outside.
        self.assertFalse(float_within(0.2, 0.0, tolerance))
        # |0.49 - 10.0| = 9.51 vs 0.1 + 0.05 * 10.0 = 0.6 -> outside.
        self.assertFalse(float_within(0.49, 10.0, tolerance))
        # |10.0 - 10.0| = 0 <= 0.6 -> inside.
        self.assertTrue(float_within(10.0, 10.0, tolerance))
        # |10.6 - 10.0| = 0.6 vs 0.6 -> boundary inside.
        self.assertTrue(float_within(10.6, 10.0, tolerance))
        # |10.7 - 10.0| = 0.7 > 0.6 -> outside.
        self.assertFalse(float_within(10.7, 10.0, tolerance))

    def test_float_within_requires_float_domain(self) -> None:
        tolerance = Tolerance(Decimal("0.1"), Decimal("0.05"))
        with self.assertRaises(VALIDATION_ERRORS):
            float_within(Decimal("10.0"), 10.0, tolerance)  # type: ignore[arg-type]
        with self.assertRaises(VALIDATION_ERRORS):
            float_within(10.0, 10, tolerance)  # type: ignore[arg-type]
        with self.assertRaises(VALIDATION_ERRORS):
            float_within(0.1, 0.1, "tol")  # type: ignore[arg-type]

    def test_decimal_to_float_is_the_one_outbound_conversion(self) -> None:
        result = decimal_to_float(Decimal("0.1"))
        self.assertIsInstance(result, float)
        self.assertEqual(result, 0.1)

    def test_decimal_to_float_requires_decimal(self) -> None:
        with self.assertRaises(VALIDATION_ERRORS):
            decimal_to_float("0.1")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        tolerance = Tolerance(Decimal("0.1"), Decimal("0.05"))
        with self.assertRaises(FrozenInstanceError):
            tolerance.abs_tol = Decimal("1")  # type: ignore[misc]


class TestLargeMagnitude(unittest.TestCase):
    """Extreme chip magnitudes keep exactness end to end."""

    MAX_INT64 = 9_223_372_036_854_775_807

    def test_to_bb_max_int64_exact(self) -> None:
        value = to_bb(self.MAX_INT64, 1)
        self.assertEqual(value, Decimal(self.MAX_INT64))
        self.assertIsInstance(value, Decimal)
        self.assertNotIsInstance(value, float)

    def test_bb_round_trip_max_int64(self) -> None:
        value = Decimal(self.MAX_INT64)
        self.assertEqual(bb_from_dict(bb_to_dict(value)), value)

    def test_allocate_total_max_int64_conserves(self) -> None:
        parts = allocate_exact([1, 1], self.MAX_INT64)
        self.assertEqual(sum(part.value for part in parts), self.MAX_INT64)

    def test_oracle_context_still_precise(self) -> None:
        # Independent oracle check on context width, not via module internals.
        with decimal.localcontext(_ORACLE_CONTEXT):
            value = Decimal(1) / Decimal(3)
        self.assertEqual(value, _oracle_div(1, 3))
        self.assertEqual(precision_module_path().suffix, ".py")


def precision_module_path() -> Path:
    """Resolve precision.py inside this worktree via the imported package."""
    return Path(poker.math.__file__).with_name("precision.py")


class TestNoBinaryFloat(unittest.TestCase):
    """Meta-test: precision.py must stay float-free outside decimal_to_float.

    The checks are implemented as :func:`collect_float_violations`, parametrized
    by source text, so the checker itself can be self-checked against synthetic
    bad snippets (proving it has teeth) and against the real module.
    """

    def _module_source(self) -> str:
        return precision_module_path().read_text(encoding="utf-8")

    def test_real_module_passes_checker(self) -> None:
        self.assertEqual(collect_float_violations(self._module_source()), [])

    def test_checker_flags_builtin_float_call(self) -> None:
        snippet = (
            "import builtins\n"
            "def decimal_to_float(value):\n"
            "    return builtins.float(value)\n"
            "def other(x):\n"
            "    return builtins.float(x)\n"
        )
        violations = collect_float_violations(snippet)
        self.assertTrue(any("float" in v and "decimal_to_float" in v for v in violations))

    def test_checker_flags_float_alias_import(self) -> None:
        for snippet in (
            "from builtins import float\n",
            "from builtins import float as real_float\n",
            "import builtins as float\n",
        ):
            with self.subTest(snippet=snippet):
                self.assertTrue(collect_float_violations(snippet))

    def test_checker_flags_int_int_division(self) -> None:
        snippet = "def bad(a, b):\n    return a / b\n"
        violations = collect_float_violations(snippet)
        self.assertTrue(any("division" in v for v in violations))

    def test_checker_allows_exact_ctor_division(self) -> None:
        snippet = "from decimal import Decimal\nvalue = Decimal(1) / Decimal(3)\n"
        self.assertEqual(collect_float_violations(snippet), [])

    def test_checker_flags_float_attribute_annotation(self) -> None:
        snippet = "import builtins\ndef bad(x: builtins.float) -> None:\n    return None\n"
        violations = collect_float_violations(snippet)
        self.assertTrue(any("annotation" in v for v in violations))

    def test_decimal_division_used_for_to_bb(self) -> None:
        # Contract: to_bb must divide Decimal values only — verified structurally
        # through the checker above plus this runtime oracle equality.
        self.assertEqual(to_bb(2500, 100), Decimal(25))
        self.assertEqual(to_bb(1, 3), _oracle_div(1, 3))


def collect_float_violations(source: str) -> list[str]:
    """Return every float-domain violation found in the given module source.

    Rules enforced over the whole module:
    - ``float(...)`` calls (bare or attribute, e.g. ``builtins.float``) are
      allowed only inside ``decimal_to_float``'s body.
    - No import binds the name ``float`` (``from builtins import float``,
      alias imports named ``float``).
    - No ``float`` annotation (name or attribute) outside the two documented
      float-domain helpers (``decimal_to_float``, ``float_within``).
    - Every true-division node either has at least one Decimal/Fraction
      constructor operand or sits inside the documented float-domain helpers;
      bare integer literals inside true division are always violations.
    """
    violations: list[str] = []
    tree = ast.parse(source)
    call_ranges = _function_line_ranges(tree, ("decimal_to_float",))
    float_ranges = _function_line_ranges(tree, _FLOAT_DOMAIN_FUNCTIONS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.asname or alias.name) == "float":
                    violations.append(f"line {node.lineno}: import binds the name 'float'")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "float" or (alias.asname or alias.name) == "float":
                    violations.append(
                        f"line {node.lineno}: import of 'float' from {node.module!r}"
                    )
        elif isinstance(node, ast.Call) and _is_float_name(node.func):
            if not _line_in_ranges(node.lineno, call_ranges):
                violations.append(f"line {node.lineno}: float() call outside decimal_to_float")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _line_in_ranges(node.lineno, float_ranges):
                continue  # the documented float-domain helpers may divide floats
            if not (_mentions_exact_ctor(node.left) or _mentions_exact_ctor(node.right)):
                violations.append(
                    f"line {node.lineno}: true division without a Decimal/Fraction operand"
                )
            for operand in (node.left, node.right):
                if (
                    isinstance(operand, ast.Constant)
                    and isinstance(operand.value, int)
                    and not isinstance(operand.value, bool)
                ):
                    violations.append(
                        f"line {node.lineno}: bare integer literal in true division"
                    )
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in _FLOAT_DOMAIN_FUNCTIONS:
            continue  # documented float-domain boundary: float annotations allowed
        candidates = [arg.annotation for arg in (*node.args.args, *node.args.kwonlyargs)]
        if node.args.vararg is not None:
            candidates.append(node.args.vararg.annotation)
        if node.args.kwarg is not None:
            candidates.append(node.args.kwarg.annotation)
        candidates.append(node.returns)
        for annotation in candidates:
            if annotation is None:
                continue
            if _is_float_name(annotation):
                violations.append(
                    f"line {node.lineno}: float annotation in {node.name}"
                )
    return violations


_FLOAT_DOMAIN_FUNCTIONS = ("decimal_to_float", "float_within")
_EXACT_CTORS = ("Decimal", "Fraction")


def _function_line_ranges(tree: ast.Module, names: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    """Map each top-level function in ``names`` to its inclusive line range."""
    ranges: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            ranges[node.name] = (node.lineno, node.end_lineno or node.lineno)
    return ranges


def _line_in_ranges(line: int, ranges: dict[str, tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in ranges.values())


def _mentions_exact_ctor(node: ast.AST) -> bool:
    """True if the subtree contains a Decimal/Fraction constructor call."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, (ast.Name, ast.Attribute)):
            name = sub.func.id if isinstance(sub.func, ast.Name) else sub.func.attr
            if name in _EXACT_CTORS:
                return True
    return False


def _is_float_name(node: ast.AST) -> bool:
    """True for a bare ``float`` name or a ``builtins.float`` attribute node."""
    if isinstance(node, ast.Name):
        return node.id == "float"
    if isinstance(node, ast.Attribute):
        return node.attr == "float"
    return False


if __name__ == "__main__":
    unittest.main()
