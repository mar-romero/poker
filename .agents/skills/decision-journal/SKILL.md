---
name: decision-journal
description: Record durable technical and quantitative decisions as concise ADR-style Markdown with plain-language learning notes and evidence.
---

# Decision Journal

Use this skill whenever a task makes or changes a **durable decision**. The purpose is twofold: preserve engineering rationale for future maintainers and create an honest learning trail that a reviewer or recruiter can inspect.

## What counts as a durable decision

Document choices that materially constrain future work or encode non-obvious reasoning, including:

- architecture, module boundaries, service/API contracts, persistence or schemas;
- mathematical definitions, estimators, priors, thresholds, tolerances or uncertainty treatment;
- algorithms, solver abstractions, Monte Carlo designs, data structures or performance strategies;
- metric semantics, event grain, numerator/denominator/opportunity definitions and feature-time rules;
- model targets, features, calibration, validation/split strategy, drift handling or baselines;
- dependencies or technologies when alternatives were plausible;
- compatibility, security, compliance, privacy or runtime boundaries;
- testing/oracle strategy when correctness depends on a deliberate choice;
- a rejected alternative whose rejection explains the current design.

Do **not** create records for formatting, obvious refactors, local naming, routine bug fixes with no lasting design choice, or choices already fully covered by an unchanged existing record.

## File convention

Store records under `docs/decisions/` using:

`ADR-####-short-kebab-title.md`

Use the next available number. Never renumber existing records. If a task changes a prior decision, prefer marking the old ADR as superseded and create a new ADR that links back to it.

Maintain `docs/decisions/README.md` as a compact index when the directory exists. The index should show ADR number/title, status, task, and one-line rationale.

## Required structure

Each decision record must contain these sections, in this order:

1. **Context / problem** — what needed to be decided and relevant constraints.
2. **Decision** — the selected option, stated precisely.
3. **Why this option** — concrete reasoning, not generic claims.
4. **Alternatives considered** — at least two plausible alternatives when they existed; explain why they were not selected.
5. **Trade-offs and consequences** — benefits, costs, risks, operational consequences, and technical debt.
6. **Validation / evidence** — tests, benchmarks, formulas, references, experiments, or observations that support the choice. Separate measured facts from assumptions.
7. **Explained simply** — explain the decision in plain language without jargon, as if teaching a developer who knows programming but not this domain.
8. **What I learned** — 2-5 concrete lessons. Do not fabricate personal experience; phrase as project learning supported by the work.
9. **How I would explain it in an interview** — a short, technically accurate explanation emphasizing problem, trade-off, evidence, and what would change the decision. Never claim the human wrote code or reasoning that was actually automated; describe AI assistance honestly if relevant.
10. **Revisit when** — observable conditions that would justify reconsideration.
11. **Related artifacts** — task IDs, code paths, tests, benchmarks, specs, datasets, or superseded ADRs.

## Evidence rules

- Never backfill rationale after the fact as if it were known beforehand. State when evidence was obtained later.
- Mark assumptions explicitly.
- Numerical claims need reproducible evidence or a source path.
- For poker/statistical work, include the population, sample, units, denominator/opportunity definition, estimator and uncertainty treatment when relevant.
- For benchmarks, include dataset/workload, hardware/runtime assumptions when known, repetitions, and metric definition.
- For model decisions, include baseline, evaluation metric, data split, feature availability time, calibration/leakage considerations and uncertainty.
- A decision record is not marketing copy. Record disadvantages and failed alternatives.

## Workflow

Before implementation, read any `decision-journaler` support findings and existing related ADRs. During implementation, keep the record aligned with what was actually built. Before returning the implementation handoff, inspect the diff and ensure every new durable decision is either represented by a new/updated ADR or explicitly covered by an existing linked ADR.

Use `REFERENCE.md` for the canonical template and examples.
