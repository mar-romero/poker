# Poker math reference

Use the codebase's normative `docs/specs/POKER_MATH_SPEC.md` as the project source of truth when present. The formulas below are compact cross-checks, not replacements for game-state definitions.

For a call of `C` into a pot of `P` before calling, break-even showdown equity in a terminal no-future-betting model is `C / (P + C)`.

For a pure bluff risking `R` to win current reward `W`, with zero equity when called and no extra branches, break-even fold frequency is `R / (R + W)`.

For a bet of `B` into pot `P`, one common heads-up indifference-model MDF is `P / (P + B)`; this is not a universal strategic mandate and is invalid when the underlying simplified game assumptions do not hold.

SPR is normally effective stack at the chosen reference point divided by pot at that same reference point. The code must document whether it is flop-start SPR or a live-street snapshot.

For weighted Bernoulli observations with nonnegative weights `w_i`, a common descriptive effective sample size is `(sum w_i)^2 / sum(w_i^2)`. It is not automatically the correct inferential degrees of freedom for every time-series model.

For independent Monte Carlo Bernoulli-like estimators, standard error scales as `O(1/sqrt(n))`; exact variance depends on the payoff/estimator, so do not hard-code the Bernoulli formula for split-pot payoff estimators without deriving it.
