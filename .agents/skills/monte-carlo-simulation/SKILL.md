---
name: monte-carlo-simulation
description: Implement reproducible Monte Carlo simulations with legal sampling, variance/error control and exact-reference convergence tests.
---

# Monte Carlo simulation

Use this skill for equity and stochastic game estimates.

- Sample only legal states after known cards, range weights and card removal.
- Make RNG algorithm/seed explicit and reproducible.
- Define estimator, sample unit and tie/split-pot treatment.
- Report sample count plus uncertainty; do not equate many iterations with correctness.
- Use exact enumeration on tractable subspaces as the primary oracle.
- Test convergence over multiple seeds and verify error scaling rather than a single lucky seed.
- If early stopping is used, base it on a documented precision target and minimum sample floor.
- Avoid biased rejection/resampling schemes; verify weighted sampling normalization.
- Bound runtime/memory and support cancellation for large simulations.
