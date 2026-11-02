"""Normalized hand-history ingest contract package.

Defines the provider-neutral boundary between venue-specific raw-text parsers
and the canonical domain model: parser adapters implement
``poker.ingest.contracts.HandAdapter`` and return ``ParseResult`` values whose
normalized hands feed ``poker.engine.state.StateEngine.replay`` unchanged.
"""

from poker.ingest import contracts

__all__ = ["contracts"]
