---
name: poker-domain-analysis
description: Model poker state, legal opportunities, actions, positions and context without smuggling strategic heuristics into facts.
---

# Poker domain analysis

Use this skill whenever poker semantics determine correctness.

- Make variant, betting structure, table size, blind/ante rules and street explicit.
- Reconstruct action order, current actor, amount to call, min/max legal raise, effective stack, pot/side pots and terminal state deterministically.
- Position labels are derived from occupied seats and button, not static seat numbers.
- Define an opportunity before recording a statistic; forced bets and all-ins may invalidate opportunities.
- Distinguish heads-up from multiway semantics.
- Keep observed facts separate from inferred ranges, population priors and strategic recommendations.
- Board texture features must be deterministic card-derived facts; strategic labels such as range advantage require a range model and assumptions.
- Reject impossible histories instead of repairing them silently.
