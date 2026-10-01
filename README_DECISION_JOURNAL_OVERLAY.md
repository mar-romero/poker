# Decision Journal Agent + Skill Overlay

Incremental overlay for the poker harness. Apply this **after** `poker-specialist-skills-agents-overlay.zip` (and therefore after `poker-harness-ready.zip`).

It adds:

- read-only `decision-journaler` agent;
- `decision-journal` skill;
- ADR-style documentation under `docs/decisions/`;
- automatic routing for non-trivial R1/R2/R3 tasks;
- implementer/planner/reviewer integration;
- plain-language learning, trade-off, evidence and interview sections.

After extraction, run:

```bash
python scripts/compile_harness.py --check
python scripts/check_harness.py
pytest -q tests/test_router.py tests/test_compile.py tests/test_skill_runtime.py tests/test_progressive_agent_budget.py tests/test_decision_journal_routing.py tests/test_poker_specialist_routing.py
```
