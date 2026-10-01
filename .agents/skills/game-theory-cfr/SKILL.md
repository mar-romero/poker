---
name: game-theory-cfr
description: Implement and validate poker game-theory solvers using toy-game correctness ladders, CFR invariants and exploitability checks.
---

# Game theory and CFR

Use this skill for CFR/CFR+, best response, exploitability and poker abstraction work.

- Freeze the game definition: players, information sets, chance, actions, utilities and terminal payoff conventions.
- Validate on Kuhn before Leduc and only then scale abstractions.
- Keep reach probability, counterfactual reach, regret and average-strategy weighting formulas explicit.
- Test deterministic traversal first; add sampling variants only after full-tree correctness.
- Verify known equilibria/exploitability on toy games within stated tolerance.
- Track convergence diagnostics over iterations and seeds; strategy appearance is not a correctness proof.
- For abstractions, document information loss and run abstraction-sensitivity tests.
- Treat real-time resolving/subgame solving as a separate algorithmic layer with explicit boundary conditions.
