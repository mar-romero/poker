---
name: poker-range-equity
description: Represent weighted card-combination ranges, blockers, conditional updates and equity with card-removal correctness.
---

# Poker ranges and equity

Use this skill for range parsing, combo enumeration, conditioning and equity.

- Treat a range as weights over legal two-card combinations; canonical Hold'em has 1,326 unordered starting combos before blockers.
- Normalize only over legal remaining combos and preserve whether weights are frequencies, probabilities or unnormalized scores.
- Apply known-card removal before counts, sampling and equity.
- Range updates must state the observation likelihood/model; do not call heuristic pruning Bayesian unless it is a valid posterior update.
- Equity must define opponents, board, remaining runouts, ties, side-pot eligibility and rake assumptions.
- Cross-check exact enumeration, Monte Carlo and symmetry fixtures on small cases.
- Expose combo-level evidence so UI summaries can be audited.
