---
name: probability-statistics
description: Specify statistically valid estimators, uncertainty, Bayesian updates, calibration and sample-size handling for poker data.
---

# Probability and statistics

Use this skill whenever observed frequencies, uncertainty or inference affect behavior.

- Define the random variable, opportunity denominator and sampling unit before an estimator.
- Separate raw frequency from smoothed estimate; expose both when useful.
- For Bernoulli opportunities, support Beta-Binomial posterior updates when a prior is justified; document prior source and sensitivity.
- Do not conflate confidence intervals and credible intervals.
- With weights/recency, report effective sample size and verify that uncertainty reflects the weighting scheme.
- For Monte Carlo estimates, report seed, sample count, standard error or interval, stopping rule and estimator definition.
- Use calibration diagnostics such as Brier score/reliability curves for probabilistic models; accuracy alone is insufficient.
- Account for dependence by player, session and time when validation would otherwise overstate certainty.
- Treat missingness, survivorship, selection bias and multiple comparisons as explicit risks.

Never emit a free-floating confidence score. Every confidence display must map to an interpretable statistical quantity or a documented deterministic rule.
