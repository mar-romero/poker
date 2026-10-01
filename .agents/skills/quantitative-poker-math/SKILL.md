---
name: quantitative-poker-math
description: Derive and validate poker mathematics with explicit units, assumptions, exact identities, approximations and reference cases.
---

# Quantitative poker math

Use this skill for pot geometry, odds, EV, sizing, stack and combinatorial calculations.

- Define chip-flow conventions before writing formulas: pot before action, amount to call, incremental investment, final pot, returned uncalled chips, rake/jackpot drops, side pots and effective stack.
- State units for every quantity and never mix percentages, probabilities, chips, big blinds or currency implicitly.
- Reduce terminal decisions to independently checkable closed forms before implementing recursive trees.
- Cover pot odds, break-even equity, fold equity, MDF, SPR, risk/reward, bluff:value ratios where assumptions make them valid, and EV for fold/call/bet/raise branches.
- For ranges, count legal card combinations after blockers; never use hand-class counts as combo counts without weights.
- Treat multiway pots, split pots, ties and rake explicitly.
- Require boundary tests: zero call, all-in, exact break-even, impossible action, capped stack, zero/100% fold probability and tie equity.

Do not encode a heuristic as a mathematical identity. When a formula depends on a simplified game tree, name that tree in the output and tests.
