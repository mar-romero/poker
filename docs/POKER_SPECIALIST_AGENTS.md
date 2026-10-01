# Poker specialist agents and quantitative skills

This overlay adds read-only specialist analysis before implementation while preserving the harness single-writer invariant: `implementer` remains the only source-code writer. Specialist findings are persisted under `.harness/runs/<TASK>/support/` and automatically injected into later stage prompts.

## Specialist agents

| Agent | Trigger | Primary responsibility |
|---|---|---|
| `quantitative-analyst` | Poker task with `important_calculation` or math/probability/statistics/equity/EV/simulation signals | Formulas, probability, estimators, uncertainty, numerical correctness and independent oracles |
| `analytics-engineer` | Poker analytics/statistics/features/schema/query/report signals | Analytical grain, legal-opportunity denominators, semantic metrics, lineage and data quality |
| `data-scientist` | Poker model/Bayesian/calibration/population/drift/prediction signals | Leakage-safe modeling, priors, calibration, validation, drift and experiment design |
| `poker-strategy-analyst` | Poker decision/range/equity/solver/GTO/exploit/board/blocker signals | Poker-state and strategic semantics, decision trees, range conditioning and solver/game-theory correctness |

Multiple specialists may be routed for the same task. That is intentional for high-correctness areas such as opponent modeling, equity and EV. They are support agents, not additional writers.

## New skills

- `quantitative-poker-math`
- `probability-statistics`
- `data-analytics-engineering`
- `data-science-modeling`
- `monte-carlo-simulation`
- `numerical-validation`
- `poker-domain-analysis`
- `poker-range-equity`
- `decision-theory`
- `game-theory-cfr`
- `experiment-design-calibration`

The router also exposes these relevant skills to planner/test/implement/review/verify roles, so the domain contract is carried into implementation and falsification rather than living only in the specialist report.

## Why these are separate

`data analytics` answers what happened and requires stable event/metric semantics. `data science` estimates/predicts latent or future behavior and therefore needs leakage control, calibration and validation. `probability/statistics` defines uncertainty and inference. `quantitative poker math` defines deterministic chip/probability identities. `game theory` validates equilibrium algorithms. Combining all of them into one generic agent makes review less falsifiable and tends to blur facts, estimates and strategic assumptions.

## Single-writer invariant

The specialist roles are read-only. They never patch implementation files. The orchestrator saves their findings before the normal lifecycle and the existing `implementer` consumes those findings in its prompt.
