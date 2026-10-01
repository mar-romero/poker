# Decision Journal Guide

The harness uses the `decision-journaler` read-only agent to identify material decisions and the `decision-journal` skill to make the single writer document those decisions in `docs/decisions/`.

The journal is designed for two audiences:

1. **The project owner learning the system:** every record includes a plain-language explanation and concrete lessons.
2. **Technical reviewers/recruiters:** every record shows alternatives, trade-offs, evidence, validation and revisit criteria instead of presenting choices as arbitrary.

The journal intentionally excludes trivial coding choices. A high-signal portfolio with 20 strong ADRs is more credible than hundreds of mechanically generated notes.

A record should never pretend that uncertainty has been resolved. If a choice is provisional, mark it `Proposed`; if later evidence changes it, create a superseding ADR instead of silently rewriting history.
