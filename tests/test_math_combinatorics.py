"""Discovery entry point for tests/math/test_combinatorics.py — bare ``-s tests``
discovery skips namespace dirs (stdlib ``math`` builds in and shadows the
sub-package name on CPython 3.12); this alias makes the combinatorics suite run
under the canonical discovery command."""

from tests.math.test_combinatorics import (
    ClassCountsTests,
    ClassRemainingCountTests,
    CombinationsCountTests,
    EnumerateTests,
    WeightedComboMassTests,
)

__all__ = [
    "ClassCountsTests",
    "ClassRemainingCountTests",
    "CombinationsCountTests",
    "EnumerateTests",
    "WeightedComboMassTests",
]
