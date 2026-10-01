# Decision Journal Reference

## Canonical ADR template

```markdown
# ADR-####: <Concise decision title>

- **Status:** Accepted | Proposed | Superseded | Deprecated
- **Date:** YYYY-MM-DD
- **Task:** <TASK-ID>
- **Decision type:** Architecture | Data | Analytics | Statistics | Modeling | Algorithm | Performance | Testing | Security | Product
- **Supersedes:** <ADR or none>
- **Superseded by:** <ADR or none>

## Context / problem

What decision was required? What constraints and unknowns mattered?

## Decision

State exactly what was selected.

## Why this option

Use concrete reasons tied to requirements, evidence and constraints.

## Alternatives considered

### Alternative A — <name>
Why it was plausible; why it was not selected.

### Alternative B — <name>
Why it was plausible; why it was not selected.

## Trade-offs and consequences

**Benefits**
- ...

**Costs / risks**
- ...

## Validation / evidence

- Deterministic tests: ...
- Benchmark/experiment: ...
- Formula/reference oracle: ...
- Assumptions still unverified: ...

## Explained simply

Explain it without specialist jargon.

## What I learned

- ...
- ...

## How I would explain it in an interview

A concise problem → options → decision → evidence → trade-off narrative.

## Revisit when

- ...

## Related artifacts

- Task: `...`
- Code: `...`
- Tests: `...`
- Specs/benchmarks: `...`
```

## Example: exact vs Monte Carlo equity

A good record would not merely say “we use Monte Carlo because it is faster.” It would specify when exact enumeration is tractable, the error/tolerance target for sampling, deterministic seeding for tests, confidence or convergence diagnostics, the performance budget, and the fallback/revisit conditions. The plain-language section might explain that exact enumeration checks every possible future card while Monte Carlo samples many possible futures and trades a small, measured error for speed.

## Example: Bayesian smoothing for sparse player statistics

Record the raw opportunity definition, prior source/population, posterior estimator, minimum evidence requirements, calibration plan and how the UI distinguishes observed rate from posterior estimate. An alternative might be raw frequency only; another might be a fixed minimum-sample threshold. Explain why smoothing is useful and the risk that a poor prior can bias early estimates.
