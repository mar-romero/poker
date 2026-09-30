# Poker Intelligence Harness Planning Pack

This directory is designed to be overlaid onto a fresh clone of the supplied Portable Agent Engineering Harness. It does **not** replace harness policy/roles/scripts.

## Included
- `planning/discovery/POKER-INTELLIGENCE-001.json`: approved PROJECT discovery bundle and complete backlog.
- Research-RDD full artifacts under `planning/research`, `planning/domain`, `planning/decisions`, `planning/scenarios`.
- Materializable project/epic/feature/spike/task definitions via the discovery bundle.
- `planning/sprints/POKER-SPRINT-001.json`: first bounded execution batch after materialization.
- `docs/specs/*`: normative math, statistics, range/equity, decision, architecture, testing, and performance specifications.

## Use in a new repository
1. Copy this pack's files over the corresponding directories in a fresh harness clone.
2. From repository root, validate Research-RDD and planning:
   `python scripts/research_discovery.py status planning/discovery/POKER-INTELLIGENCE-001.json`
   `python scripts/product_planning.py validate planning/discovery/POKER-INTELLIGENCE-001.json`
3. Materialize all approved project/epic/feature/task artifacts:
   `python scripts/product_planning.py materialize planning/discovery/POKER-INTELLIGENCE-001.json`
4. Inspect `planning/sprints/POKER-SPRINT-001.json`.
5. Start the first unblocked task through the harness provider activation flow rather than editing application code directly.

The pack intentionally creates a broad approved backlog, but only Sprint 001 is scheduled initially. Later sprints should be composed from ready tasks as dependencies close and evidence changes.
