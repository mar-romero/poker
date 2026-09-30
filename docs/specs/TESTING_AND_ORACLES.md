# Testing and Oracle Strategy

## Layers
1. Pure unit tests for formulas, cards, combo counts, rules, and stat predicates.
2. Golden-hand fixtures for parser -> state -> feature -> stat behavior.
3. Property/metamorphic tests for conservation, uniqueness, probability/range invariants, and deterministic replay.
4. Independent oracle cross-checks for hand evaluator/equity/numerical functions where feasible.
5. End-to-end scenarios corresponding to `planning/scenarios/POKER-INTELLIGENCE-001.json`.
6. Performance regression benchmarks with machine-readable results.

## Critical invariants
- no duplicate physical cards
- chip conservation apart from explicit rake/fee
- legal actor/order/raise rules
- stat success subset of opportunity set
- impossible range combos have zero mass
- normalized probability masses in bounds
- equity shares sum to one within tolerance
- seeded simulations reproducible
- strategy action probabilities normalize within tolerance
- recommendations preserve provenance and approximation labels

## Red/green discipline
Important calculations require behavioral RED evidence before implementation changes and GREEN after minimal correctness. Tooling/import failures are not valid RED.
