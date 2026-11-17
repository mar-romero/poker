"""Derived-features tables: feature_decisions + feature_opportunities.

Revision ID: c5e9b1f4a7d3
Revises: a1c9f2b4e6d8
Create Date: 2026-11-13

Task POKER-DATA-FEATURES-001 (risk R2): creates the two versioned DERIVED
feature tables added to ``poker.db.models.Base.metadata`` — a deliberate
documented extension of PS-3 (11 raw tables + 2 derived tables = 13 planned
tables; see ``tests/db/test_schema.py::EXPECTED_TABLES``). Both
``op.create_table`` blocks mirror the ORM DDL exactly (column order, types,
NOT NULL, PK, FK targets, every CHECK expression, both UNIQUE constraints and
the ``created_at`` server_default) so the ADR-0002 "alembic-vs-create_all
parity oracle" stays green.

DELEGATION TRAP — deliberate, not silent: the initial revision a1c9f2b4e6d8
delegates its ``upgrade()`` to the LIVE ``Base.metadata``, which now includes
these two ORM tables. On a FRESH database ``alembic upgrade head`` therefore
already creates both feature tables during the initial revision, and
UNCONDITIONAL ``op.create_table`` calls here would fail with "table already
exists". The explicit blocks below are guarded by an existence check so they
stay authoritative for databases upgraded from the 11-table baseline while
fresh upgrades remain green. The parity oracle exercises the explicit-create
path directly by stamping back to the initial revision and dropping the two
tables before re-running ``upgrade head`` (tests/db/test_schema.py,
tests/features/test_materialize.py::test_S02).

downgrade drops both tables in reverse dependency order; raw normalized data
is untouched by both directions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5e9b1f4a7d3"
down_revision = "a1c9f2b4e6d8"
branch_labels = None
depends_on = None

_CREATED_AT_SERVER_DEFAULT = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def _existing_tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_tables(bind)
    if "feature_decisions" not in existing:
        op.create_table(
            "feature_decisions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("hand_id", sa.Integer(), nullable=False),
            sa.Column("action_id", sa.Integer(), nullable=False),
            sa.Column("feature_version", sa.Text(), nullable=False),
            sa.Column("seat_number", sa.Integer(), nullable=False),
            sa.Column("position", sa.Text(), nullable=False),
            sa.Column("initiative", sa.Integer(), nullable=False),
            sa.Column("pot_type", sa.Text(), nullable=False),
            sa.Column("effective_stack_milli_bb", sa.Integer(), nullable=False),
            sa.Column("facing_action", sa.Text(), nullable=False),
            sa.Column("sizing_bucket", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=sa.text(_CREATED_AT_SERVER_DEFAULT),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["hand_id"], ["hands.id"]),
            sa.ForeignKeyConstraint(["action_id"], ["actions.id"]),
            sa.CheckConstraint("seat_number BETWEEN 2 AND 10"),
            sa.CheckConstraint("initiative IN (0, 1)"),
            sa.CheckConstraint("effective_stack_milli_bb >= 0"),
            sa.UniqueConstraint("hand_id", "action_id", "feature_version"),
        )
    if "feature_opportunities" not in existing:
        op.create_table(
            "feature_opportunities",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("hand_id", sa.Integer(), nullable=False),
            sa.Column("action_id", sa.Integer(), nullable=True),
            sa.Column("feature_version", sa.Text(), nullable=False),
            sa.Column("street", sa.Integer(), nullable=False),
            sa.Column("seat_number", sa.Integer(), nullable=False),
            sa.Column("legal_actions", sa.Text(), nullable=False),
            sa.Column("board_descriptor", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=sa.text(_CREATED_AT_SERVER_DEFAULT),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["hand_id"], ["hands.id"]),
            sa.ForeignKeyConstraint(["action_id"], ["actions.id"]),
            sa.CheckConstraint("street BETWEEN 0 AND 3"),
            sa.CheckConstraint("seat_number BETWEEN 2 AND 10"),
            sa.UniqueConstraint("hand_id", "street", "seat_number", "feature_version"),
        )


def downgrade() -> None:
    op.drop_table("feature_opportunities")
    op.drop_table("feature_decisions")
