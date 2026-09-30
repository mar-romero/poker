# Planning Validation

Validated against the supplied Portable Agent Engineering Harness on 2026-09-30.

- `python scripts/research_discovery.py status planning/discovery/POKER-INTELLIGENCE-001.json` -> ready: true; no missing/failing full Research-RDD artifacts.
- `python scripts/product_planning.py validate planning/discovery/POKER-INTELLIGENCE-001.json` -> ok: true; 70 work items; 77 tasks; 0 blocking questions.
- `python scripts/product_planning.py materialize planning/discovery/POKER-INTELLIGENCE-001.json` -> ok: true; all work items/tasks and Sprint 001 materialized.
- Custom integrity check -> all JSON parses; all task dependencies and parent IDs resolve.

The first sprint uses 10/12 default capacity units to leave integration slack.
