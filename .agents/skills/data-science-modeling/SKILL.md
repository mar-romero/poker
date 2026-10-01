---
name: data-science-modeling
description: Build opponent/population models with leakage control, calibrated probabilities, drift handling and reproducible validation.
---

# Data science modeling

Use this skill for opponent profiles, population models, archetypes, behavioral prediction and learned decision inputs.

- Define target and prediction time first; features must be available at that time.
- Establish a simple baseline before complex models.
- Split validation by time and, where needed, by player/session/table to prevent identity and future leakage.
- Prefer calibrated probabilities and uncertainty over hard labels.
- Measure calibration, discrimination and task-specific utility separately.
- Handle sparse players with hierarchical/population priors rather than unstable point estimates.
- Treat recency weighting and change-point/drift detection as modeling choices with backtests, not assumptions.
- Record feature version, data interval, random seed, hyperparameters, model artifact/version and evaluation dataset.
- Never present clustering/archetypes as ground truth; they are descriptive partitions unless independently validated.
- Do not make causal claims from observational poker histories without an identification design.
