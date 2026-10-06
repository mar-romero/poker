"""Discovery entry point for tests/math/test_precision.py — bare ``-s tests``
discovery skips namespace dirs (stdlib ``math`` builds in and shadows the
sub-package name on CPython 3.12); this alias makes the precision suite run
under the canonical discovery command."""

from tests.math.test_precision import (
    TestFromBB,
    TestLargeMagnitude,
    TestNoBinaryFloat,
    TestRoundingAndAllocation,
    TestSerialization,
    TestStakesRoundTrip,
    TestToBB,
    TestTolerance,
)

__all__ = [
    "TestFromBB",
    "TestLargeMagnitude",
    "TestNoBinaryFloat",
    "TestRoundingAndAllocation",
    "TestSerialization",
    "TestStakesRoundTrip",
    "TestToBB",
    "TestTolerance",
]
