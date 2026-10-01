---
name: experiment-design-calibration
description: Design reproducible backtests and calibration experiments for probabilistic poker models and strategy components.
---

# Experiment design and calibration

Use this skill when deciding whether a model, heuristic or optimization actually improves the system.

- State hypothesis, primary metric, baseline, dataset interval and exclusion rules before evaluation.
- Use temporal/player-group splits that match deployment and prevent leakage.
- Report uncertainty and effect size, not only point improvements.
- Calibrate probabilistic outputs out of sample; keep calibration data separate from final evaluation when practical.
- Compare against simple priors/heuristics and run ablations for complex feature sets.
- For strategy components, separate predictive quality from EV/regret impact.
- Record seeds/configuration and make experiments rerunnable from code/artifacts.
- Avoid repeated tuning on the test set; create a new holdout when it has become part of development.
