"""Initial poker data-store schema: 11 planned tables + card-disjoint triggers.

Revision ID: a1c9f2b4e6d8
Revises: (none)
Create Date: 2026-10-06

The upgrade creates all 11 planned tables exactly as
``poker.db.models.Base.metadata`` defines them — INCLUDING the deferrable
composite foreign key ``hands(id, button_seat) ->
hand_players(hand_id, seat_number)`` and the per-hand card-disjointness
triggers — by delegating to that same single authoritative metadata (DJ:
ADR-0002 "alembic-vs-create_all parity oracle"; ADR-0003).

Rationale: the TDD oracle compares PRAGMA table_info and
PRAGMA foreign_key_list per table between an Alembic-upgraded database and a
create_all database and requires EXACT equality. Delegating the initial
revision to the shared metadata makes drift structurally impossible for the
initial schema. Future revisions MUST use explicit op.* steps and are then
verified against this frozen baseline via alembic history + the parity oracle
below.

No production data exists before this revision; downgrade drops every table
(and their triggers, which SQLite discards together with their tables).
"""

from __future__ import annotations

from alembic import op

revision = "a1c9f2b4e6d8"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from poker.db.models import Base

    # Creates the 11 tables and the card-disjointness triggers (the triggers
    # are attached to Base.metadata's after_create), PS-3/PS-9.
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    from poker.db.models import Base

    Base.metadata.drop_all(op.get_bind())
