# Architecture Specification

```text
Structured hand/live adapters
        -> normalization + provenance
        -> deterministic game state / decision snapshots
        -> operational DB + versioned derived features
        -> stats DSL / opponent+population model
        -> board + weighted range model
        -> exact/MC equity + poker math
        -> baseline strategy / solver / exploit adjustment
        -> decision EV + confidence + explanation
        -> FastAPI/CLI + PySide6 HUD
        -> replayer / grading / leak detection / trainer / reports
```

## Repository target layout
```text
src/poker/
  domain/ engine/ ingest/ db/ features/ stats/ math/ cards/ range/ equity/
  opponents/ strategy/ solver/ decision/ adapters/ api/ hud/ learn/
tests/
  fixtures/ domain/ engine/ ingest/ db/ stats/ math/ cards/ range/ equity/
  opponents/ strategy/ solver/ decision/ hud/ learn/ property/ security/
benchmarks/
data/strategies/
docs/specs/
docs/adr/
migrations/
native/                 # only after profiling proves need
```

## Architectural rules
- Domain/math modules do not import UI or provider adapters.
- Raw inputs are immutable; derived features can be recomputed by version.
- Every analytical result carries definition/model/strategy version and provenance.
- Long-running simulation/solver work is cancellable and cannot block the UI thread.
- Strategy source is pluggable: imported, precomputed, abstract lookup, or local solver.
- Live mode and post-session mode share the same canonical state/decision contracts.
- SQLite is the default local operational store; interfaces must not couple domain logic to one SQL dialect.
- Native acceleration is optional and must have a Python parity oracle.
