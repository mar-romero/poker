# Decision Journal

This directory contains Architecture Decision Record (ADR)-style notes for durable project decisions. Records are intentionally concise, evidence-backed, and include a plain-language learning section.

## Index

| ADR | Status | Task | Decision |
|---|---|---|---|
| [ADR-0002](adr-0002-poker-db-test-contract.md) | Accepted | POKER-DATA-SCHEMA-001 | Test-facing poker.db API/schema contract; seats stay 2..10 (23-card full ring, not 25); deferred composite button FK. |
| [ADR-0003](adr-0003-poker-db-session-contract.md) | Accepted | POKER-DATA-SCHEMA-001 | Factory ensures schema idempotently; PEP-249 autocommit=False + pragma autocommit window; card-disjoint triggers on metadata; initial migration delegates to shared metadata. |

Add one row whenever a new ADR is accepted. Do not renumber existing ADRs.
