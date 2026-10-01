---
name: decision-theory
description: Evaluate poker actions through explicit decision trees, EV decomposition, uncertainty and sensitivity rather than opaque recommendations.
---

# Decision theory

Use this skill for action comparison and recommendation logic.

- Enumerate legal candidate actions and sizings first.
- Define mutually exclusive/exhaustive opponent-response branches; branch probabilities must normalize.
- Condition opponent ranges per branch before equity/continuation value.
- Decompose EV into branch contributions and incremental hero investment.
- Keep model uncertainty separate from Monte Carlo sampling error.
- Run sensitivity analysis on high-leverage inputs such as fold probability, range weights, rake and future-node values.
- Report near-ties when EV differences are smaller than uncertainty/tolerance.
- Separate baseline strategy, exploit adjustment and learning explanation.
- Recommendation output must include assumptions and enough decomposition to reproduce the result.
