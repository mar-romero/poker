---
name: data-analytics-engineering
description: Design traceable poker analytical data models, semantic metrics, opportunity denominators, features and reproducible queries.
---

# Data analytics engineering

Use this skill for ingestion, normalized storage, derived features, statistics engines, reports and analytical queries.

- Declare table/event grain and stable identifiers before adding metrics.
- Preserve immutable raw/normalized facts; derived features and metrics must be versioned and recomputable.
- A poker statistic is a numerator over explicitly enumerated legal opportunities. Store or reproducibly derive the opportunity predicate.
- Keep semantic dimensions explicit: game type, table size, position, effective stack, street, initiative, pot type, facing action, sizing bucket, board class, heads-up/multiway and time window.
- Prevent double counting across retries/imports with deterministic identities and idempotent ingestion.
- Trace every aggregate back to hand, player and decision snapshot.
- Validate null semantics, duplicated hands, partial histories, aliases and late-arriving corrections.
- Benchmark high-cardinality filtered queries and prefer materialization only when the semantic source remains canonical.

Metric names are API contracts. A filter must not silently change the denominator definition.
