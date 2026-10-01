---
name: numerical-validation
description: Validate floating-point and numerical algorithms with tolerances, invariants, reference oracles and adversarial boundaries.
---

# Numerical validation

Use this skill for high-correctness mathematical code.

- Choose numeric representation deliberately; document where integers/rationals/Decimal/float are used and why.
- Compare floats with scale-aware tolerances, not exact equality unless representation guarantees it.
- Check conservation invariants: chips, probability mass, normalized range weight, branch probability and pot construction.
- Use independent oracles: analytic identities, exhaustive enumeration, trusted fixtures or a separately implemented reference.
- Add property tests for monotonicity, symmetry, permutation/card-suit invariance and impossible-state rejection.
- Stress near-zero probabilities, huge/small pots, ties, rounding boundaries and normalization of sparse weighted ranges.
- Never validate an algorithm solely against code that shares the same implementation path.
