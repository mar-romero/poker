"""Declarative schema for the poker data store (SQLite-first, portable SQL).

Authoritative metadata for BOTH schema paths:

- ``Base.metadata.create_all(engine)`` (deterministic recreation, tests), and
- the initial Alembic revision (see ``migrations/versions/``), which delegates
  to this same metadata so the two paths can never drift (DJ: ADR-0002
  "alembic-vs-create_all parity oracle").

Design rules (verified by ``tests/db/test_schema.py``):

- PS-3: exactly 11 planned tables (no extras, no fewer).
- PS-4: every chip/amount column is INTEGER storage; never REAL/FLOAT/DOUBLE.
- PS-13: timestamps are TEXT ISO-8601 UTC with database-level ``DEFAULT``
  expressions so raw-SQL writers work without ORM hooks; portable types only
  (INTEGER/TEXT), no JSON columns, no BLOBs.
- Ports of the canonical domain rules are enforced at the DATABASE level with
  CHECK/UNIQUE/FK/trigger mechanisms, never ORM-only, because the oracle uses
  raw SQL.
- Inline "# PS-n" comments map columns/constraints to the 14 product
  statements from the POKER-DATA-SCHEMA-001 Phase-B contract; "# DJ:" comments
  reference decision-journal records (ADR-0002/ADR-0003).

PS numbering note: the Phase-B task brief lists the product statements 1..14;
no repo-local canonical PS registry was found, so this mapping is stated here
explicitly for review rather than guessed silently.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------- constants

# DJ: ADR-0002 — card domain CHECK shared by all card columns (PS-8).
_CARD_RANKS_SQL = "('2','3','4','5','6','7','8','9','T','J','Q','K','A')"
_CARD_SUITS_SQL = "('c','d','h','s')"
_CARD_CHECK_SQL = (
    "length(card) = 2"
    f" AND substr(card, 1, 1) IN {_CARD_RANKS_SQL}"
    f" AND substr(card, 2, 1) IN {_CARD_SUITS_SQL}"
)
_LEGAL_SEAT_SQL = "seat_number BETWEEN 2 AND 10"  # PS-10 (canonical Seat 2..10)
_LEGAL_STREET_SQL = "street BETWEEN 0 AND 3"  # PS-11

_ACTION_TYPES_SQL = "('FOLD','CHECK','CALL','BET','RAISE','POST')"
_POST_TYPES_SQL = "('sb','bb','ante')"
# PS-6 + PS-7: amountless FOLD/CHECK; valued actions need amount + semantics;
# POST needs amount + sb/bb/ante post_type + NULL semantics.
_ACTION_SHAPE_SQL = (
    "(action_type IN ('FOLD','CHECK')"
    " AND amount IS NULL AND semantics IS NULL AND post_type IS NULL)"
    " OR (action_type IN ('CALL','BET','RAISE')"
    " AND amount IS NOT NULL AND semantics IS NOT NULL AND post_type IS NULL)"
    " OR (action_type = 'POST' AND amount IS NOT NULL AND semantics IS NULL"
    " AND post_type IS NOT NULL"
    f" AND post_type IN {_POST_TYPES_SQL})"
)

# PS-13: database-level UTC ISO-8601 timestamps so raw-SQL fixtures work.
_UTC_NOW_SQL = text("strftime('%Y-%m-%dT%H:%M:%fZ','now')")
_ZERO_SQL = text("0")


def _card_check() -> CheckConstraint:
    return CheckConstraint(_CARD_CHECK_SQL)  # PS-8 (per-table instance)


class Base(DeclarativeBase):
    """Declarative base; ``Base.metadata`` is the single schema authority."""


# ------------------------------------------------------------------- tables


class RawSource(Base):
    """Origin of raw poker hand content (site dump, file, stream)."""

    __tablename__ = "raw_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Intentionally NOT unique: the same external source name may be imported
    # from separate fixtures; auditability keys live on hands (PS-12).
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # PS-13 (+DJ: ADR-0003).
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )


class ImportBatch(Base):
    """Unit of raw hand ingestion (one batch may cover many parsed hands)."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("raw_sources.id"),  # PS-12 provenance
        nullable=False,
    )
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)  # PS-13
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)  # PS-13
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )


class Player(Base):
    """Canonical player identity; aliases map parser spellings onto it."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # identity merge happens via aliases, so name is not unique
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )

    __table_args__ = (
        CheckConstraint("length(name) > 0"),  # PS-2: no empty player identity
    )


class PlayerAlias(Base):
    """Raw parser-side spelling that resolves to a canonical Player."""

    __tablename__ = "player_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id"), nullable=False  # PS-12
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )


class Hand(Base):
    """One parsed poker hand with dedup/audit provenance keys."""

    __tablename__ = "hands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_sources.id"), nullable=False  # PS-12
    )
    import_batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("import_batches.id"), nullable=False  # PS-12
    )
    table_name: Mapped[str] = mapped_column(Text, nullable=False)  # PS-12 dedup key
    hand_number: Mapped[str] = mapped_column(Text, nullable=False)  # PS-12 dedup key
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)  # PS-12
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)  # PS-12
    # PS-4 chip columns are INTEGER (never REAL/FLOAT/DOUBLE); PS-5 non-negative.
    small_blind: Mapped[int] = mapped_column(Integer, nullable=False)
    big_blind: Mapped[int] = mapped_column(Integer, nullable=False)
    ante: Mapped[int] = mapped_column(Integer, nullable=False)
    # PS-10: exactly one button per hand, expressed as a legal seat, NOT NULL;
    # DJ: ADR-0002 — deferred composite FK (single-transaction hand writes).
    button_seat: Mapped[int] = mapped_column(Integer, nullable=False)
    # PS-11: playback reaches at most the river (0..3).
    street_reached: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=_ZERO_SQL
    )
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )

    __table_args__ = (
        UniqueConstraint(
            "raw_source_id", "table_name", "hand_number", "parser_version"
        ),  # PS-12 dedup (same source must not re-import same hand+parser)
        UniqueConstraint("content_sha256", "parser_version"),  # PS-12 re-parse auditability
        CheckConstraint("small_blind >= 0 AND big_blind >= 0 AND ante >= 0"),  # PS-5
        CheckConstraint("button_seat BETWEEN 2 AND 10"),  # PS-10
        CheckConstraint("street_reached BETWEEN 0 AND 3"),  # PS-11
        ForeignKeyConstraint(
            ["id", "button_seat"],
            ["hand_players.hand_id", "hand_players.seat_number"],
            # DJ: ADR-0002/ADR-0003 — deferrable so the hand row, its seated
            # players, actions and cards commit inside ONE transaction.
            deferrable=True,
            initially="DEFERRED",
        ),
    )


class HandPlayer(Base):
    """Seated player at one hand (seat occupies 2..10, per canonical Seat)."""

    __tablename__ = "hand_players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hands.id"), nullable=False  # PS-12 lineage
    )
    seat_number: Mapped[int] = mapped_column(Integer, nullable=False)  # PS-10
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id"), nullable=False  # PS-12 lineage
    )
    stack: Mapped[int] = mapped_column(Integer, nullable=False)  # PS-4 chips
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )

    __table_args__ = (
        UniqueConstraint("hand_id", "seat_number"),  # one row per seat per hand
        UniqueConstraint("hand_id", "player_id"),  # one row per player per hand
        CheckConstraint(_LEGAL_SEAT_SQL.replace("seat_number", "seat_number")),  # PS-10
        CheckConstraint("stack >= 0"),  # PS-5
    )


class Action(Base):
    """One player action sequence step within a hand (identity = hand+sequence)."""

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hands.id"), nullable=False  # PS-12 lineage
    )
    seat_number: Mapped[int] = mapped_column(Integer, nullable=False)  # who acted
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id"), nullable=False  # PS-12 lineage
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    street: Mapped[int] = mapped_column(Integer, nullable=False)  # PS-11
    action_type: Mapped[str] = mapped_column(Text, nullable=False)  # PS-7 domain
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)  # PS-4/PS-5/PS-6
    semantics: Mapped[str | None] = mapped_column(Text, nullable=True)  # PS-6
    post_type: Mapped[str | None] = mapped_column(Text, nullable=True)  # PS-7
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )

    __table_args__ = (
        UniqueConstraint("hand_id", "sequence"),
        CheckConstraint(_LEGAL_STREET_SQL),  # PS-11
        CheckConstraint(f"action_type IN {_ACTION_TYPES_SQL}"),  # PS-7
        CheckConstraint("amount IS NULL OR amount >= 0"),  # PS-5
        CheckConstraint(_ACTION_SHAPE_SQL),  # PS-6 + PS-7
        ForeignKeyConstraint(
            ["hand_id", "seat_number"],
            ["hand_players.hand_id", "hand_players.seat_number"],
            # actions may only be attributed to seats of the SAME hand.
        ),
    )


class BoardCard(Base):
    """Community card of a hand; per-hand uniqueness + cross-table disjointness."""

    __tablename__ = "board_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hands.id"), nullable=False  # PS-12 lineage
    )
    card: Mapped[str] = mapped_column(Text, nullable=False)  # PS-8
    street: Mapped[int] = mapped_column(Integer, nullable=False)  # PS-11
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )

    __table_args__ = (
        UniqueConstraint("hand_id", "card"),  # PS-9 per-hand uniqueness
        _card_check(),  # PS-8
        CheckConstraint(_LEGAL_STREET_SQL),  # PS-11
        CheckConstraint("position BETWEEN 1 AND 3"),
    )


class HoleCard(Base):
    """Private card of a seated hand_player; per-hand uniqueness + disjointness."""

    __tablename__ = "hole_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hand_id: Mapped[int] = mapped_column(Integer, nullable=False)  # PS-12 lineage
    seat_number: Mapped[int] = mapped_column(Integer, nullable=False)  # PS-10
    card: Mapped[str] = mapped_column(Text, nullable=False)  # PS-8
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )

    __table_args__ = (
        UniqueConstraint("hand_id", "card"),  # PS-9 per-hand uniqueness
        _card_check(),  # PS-8
        CheckConstraint(_LEGAL_SEAT_SQL),  # PS-10
        ForeignKeyConstraint(
            ["hand_id", "seat_number"],
            ["hand_players.hand_id", "hand_players.seat_number"],
            # hole cards may only belong to seats of the SAME hand.
        ),
    )


class UncalledReturn(Base):
    """Chips not contested (per pot accounting) returned to their owner."""

    __tablename__ = "uncalled_returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hands.id"), nullable=False  # PS-12 lineage
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("players.id"), nullable=False  # PS-12 lineage
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # PS-4/PS-5 chips
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )

    __table_args__ = (
        CheckConstraint("amount >= 0"),  # PS-5
    )


class DecisionPoint(Base):
    """Identity-only marker joining decisive actions for later analytics."""

    __tablename__ = "decision_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hands.id"), nullable=False  # PS-12 lineage
    )
    action_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("actions.id"), nullable=True  # PS-14: action may be NULL
    )
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=_UTC_NOW_SQL
    )
    # PS-14: identity-only — no extra columns are allowed; the TDD oracle
    # whitelists PRAGMA table_info to {id, hand_id, action_id, created_at}.
    __table_args__ = ()


# ------------------------------------------------------------------ triggers

# PS-9 + DJ: ADR-0002 — schema-level per-hand card ledger: a card may not be
# both a hole card and a board card within the same hand. Both directions are
# enforced by triggers so raw SQL (not just the ORM) hits them. Triggers are
# attached to the metadata itself so ANY ``Base.metadata.create_all`` —
# including the test oracle, the schema-ensuring engine factory and the
# initial Alembic revision — creates them. ``IF NOT EXISTS`` makes the block
# idempotent (SQLite stores normalized trigger DDL without it), so repeated
# create_all calls cannot fail or alter the stored sqlite_master text.
_CARD_DISJOINT_TRIGGER_SQL = (
    "CREATE TRIGGER IF NOT EXISTS trg_hole_card_not_on_board BEFORE INSERT ON"
    " hole_cards FOR EACH ROW WHEN EXISTS ("
    "  SELECT 1 FROM board_cards b"
    "  WHERE b.hand_id = NEW.hand_id AND b.card = NEW.card)"
    " BEGIN SELECT RAISE(ROLLBACK,"
    " 'check failed: card is already on the board of this hand'); END",
    "CREATE TRIGGER IF NOT EXISTS trg_board_card_not_in_holes BEFORE INSERT ON"
    " board_cards FOR EACH ROW WHEN EXISTS ("
    "  SELECT 1 FROM hole_cards h"
    "  WHERE h.hand_id = NEW.hand_id AND h.card = NEW.card)"
    " BEGIN SELECT RAISE(ROLLBACK,"
    " 'check failed: card is already in the holes of this hand'); END",
)


@event.listens_for(Base.metadata, "after_create")
def _create_card_disjointness_triggers(target, connection, **kw):
    for statement in _CARD_DISJOINT_TRIGGER_SQL:
        connection.execute(text(statement))
