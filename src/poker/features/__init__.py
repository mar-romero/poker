"""Versioned derived features over the normalized poker data store.

Public API (task POKER-DATA-FEATURES-001, planner step 10 pin):

* ``materialize_features(engine, hand_ids=None, feature_version=FEATURES_VERSION)``
  — materialize/recompute feature rows (see :mod:`poker.features.materialize`).
* ``MaterializeResult`` — frozen counts dataclass.
* ``FEATURES_VERSION`` — feature-semantics cache key ('1.0.0').
"""

from .materialize import FEATURES_VERSION, MaterializeResult, materialize_features
from .schema import (
    FACING_ACTIONS,
    POSITIONS,
    POT_TYPES,
    SIZING_BUCKETS,
    FEATURES_VERSION as _SCHEMA_FEATURES_VERSION,
)

__all__ = [
    "FEATURES_VERSION",
    "MaterializeResult",
    "materialize_features",
    "FACING_ACTIONS",
    "POSITIONS",
    "POT_TYPES",
    "SIZING_BUCKETS",
]

# Single source of truth: re-export the schema constant (asserted identical).
assert FEATURES_VERSION == _SCHEMA_FEATURES_VERSION
