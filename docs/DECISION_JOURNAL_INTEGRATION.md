# Decision Journal Integration

## Components

- **Agent:** `decision-journaler` — read-only. Identifies which durable decisions deserve documentation and challenges weak or retrospective rationale.
- **Skill:** `decision-journal` — used by the single writer to create/update ADRs under `docs/decisions/`.
- **Routing:** non-trivial R1/R2/R3 tasks include the journaler and skill automatically. Mechanical R0 work does not.
- **Review:** the normal reviewer receives the skill and checks that documented rationale matches the frozen candidate and available evidence.

## Why both an agent and a skill?

The agent provides independent reasoning and cannot rewrite history. The skill gives the implementer a stable format and evidence rules. This preserves the harness single-writer invariant while making decision capture part of the implementation workflow.

## Portfolio behavior

Each high-signal ADR contains a simple explanation, lessons, interview framing, alternatives and revisit conditions. The goal is not to claim that every choice was perfect; it is to show that important choices were explicit, testable and revisable.
