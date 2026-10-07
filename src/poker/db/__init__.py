"""SQLite persistence layer for the poker intelligence platform.

Subpackages/modules:
- ``poker.db.models``: declarative schema (single authoritative metadata).
- ``poker.db.session``: WAL-configured engine/session factory.

Contract sources: ``tests/db/test_schema.py`` (TDD oracle) and
``docs/decisions/adr-0002-poker-db-test-contract.md`` (ADR-0002).
"""

# Schema/provenance product statements are annotated inline in models/session
# modules as "# PS-n" (numbering follows the Phase-B task contract's 14
# product-statement bullets; no repo-local canonical PS registry exists — see
# handoff residual risk) and "# DJ:" references to docs/decisions/ records.
