# Poker Intelligence Project Roadmap

This roadmap is supplementary; the harness source of truth is the approved discovery bundle and materialized `tasks/*.json`. A harness sprint is a bounded execution batch, not a calendar promise.

## Phase 1 — Trustworthy core
Sprint 001 (materialized): domain, pot accounting, numeric precision, hand evaluator, DB schema. Next: state machine, parser contract/reference parser, golden corpus.

## Phase 2 — Statistics + math vertical slice
Opportunity engine, base preflop/postflop stats, DSL, Bayesian uncertainty, rake/EV/SPR/combinatorics. Demonstrate correct VPIP/PFR/3bet/cbet and filtered BB-vs-BTN queries from imported hands.

## Phase 3 — Ranges + equity
Weighted 1,326-combo ranges, notation, board texture, range classes/blockers, exact and Monte Carlo equity, multiway legality.

## Phase 4 — Opponent/population intelligence
Profiles, recency, population priors, archetypes, change detection, evidence-backed automatic notes.

## Phase 5 — Strategy + decision engine
Strategy schema/imports, preflop/postflop baselines, action/sizing generator, branch EV, exploit adjustment, mixed recommendation, sensitivity, explanation.

## Phase 6 — Study product
Replayer, grading, leak detection, trainer, spaced repetition, session/longitudinal reports, exports.

## Phase 7 — HUD/live integration
Overlay shell, seats, stat panels/popups, structured live-data spike/adapter, decision panel, stale-data protections, latency tuning.

## Phase 8 — Solver R&D
Solver architecture ADR, Kuhn/Leduc CFR correctness, Holdem abstractions, re-solving prototype, strategy compression, convergence/performance benchmarks. Solver features may run in parallel after the mathematical/range contracts are stable.

## Phase 9 — Hardening and acceleration
Property tests, benchmark gates, observability/provenance, security/privacy recovery, native acceleration only for measured hotspots.
