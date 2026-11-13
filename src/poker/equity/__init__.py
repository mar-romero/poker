"""Exact-equity package for poker (POKER-EQUITY-EXACT-001).

v1.0.0 scope: river-only heads-up EXACT equity by full enumeration of live
opponent combos with exact ``fractions.Fraction`` arithmetic. The durable
module spec — including the out-of-scope pins (multiway equity owned by
POKER-EQUITY-MULTI-001, pre-river/turn exact enumeration deferred to 1.1.0,
sampling-based estimation owned by POKER-EQUITY-MC-001, float API and
per-combo equity map) — lives in :mod:`poker.equity.exact`.
"""

from poker.equity.exact import (
    EQUITY_EXACT_VERSION,
    EquityResult,
    exact_river_equity,
)

__all__ = ["EQUITY_EXACT_VERSION", "EquityResult", "exact_river_equity"]
