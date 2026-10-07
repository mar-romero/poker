"""Behavior-defining schema tests for the poker database layer (TDD Phase A RED).

Two-phase RED design: no top-level import of ``poker.db`` or any
SQLAlchemy-on-poker.db code. Every test reaches the database layer through the
lazy ``_import_db()`` helper inside ``setUp``, so in Phase A each test reports
the same behavioral failure (``ModuleNotFoundError: No module named
'poker.db'``) individually.

Public API contract exercised here (the Phase-B implementation must provide):

- ``poker.db``, ``poker.db.models`` and ``poker.db.session`` are importable.
- ``poker.db.session.create_engine(db_path) -> sqlalchemy.Engine``: file-backed
  SQLite engine at ``db_path`` whose connections run with
  ``PRAGMA foreign_keys=ON``, ``PRAGMA journal_mode=WAL`` and a positive
  ``PRAGMA busy_timeout``.
- ``poker.db.models.Base.metadata.create_all(engine)`` recreates the full
  schema deterministically.
- ``migrations/`` at the repository root is a runnable Alembic script directory
  whose ``env.py`` reads the ``sqlalchemy.url`` main option so tests can run
  ``alembic upgrade head`` headless against an arbitrary SQLite path.

Schema contract enforced by these tests (raw SQL, ORM-independent):

- 11 planned tables: raw_sources, import_batches, players, player_aliases,
  hands, hand_players, actions, board_cards, hole_cards, uncalled_returns,
  decision_points.
- Chip amounts are INTEGER storage (never REAL/FLOAT/DOUBLE) and non-negative.
- hands: UNIQUE(raw_source_id, table_name, hand_number, parser_version),
  UNIQUE(content_sha256, parser_version), content_sha256/parser_version NOT
  NULL, button_seat NOT NULL with a composite foreign key
  hands(id, button_seat) -> hand_players(hand_id, seat_number) that must stay
  satisfiable within one transaction (deferred enforcement is acceptable:
  immediate-at-execute OR deferred-at-commit both pass the negative helper).
- hand_players: UNIQUE(hand_id, seat_number), UNIQUE(hand_id, player_id),
  seat_number CHECK (seat_number BETWEEN 2 AND 10) -- matching the canonical
  domain model (src/poker/domain/game.py: "A physical table seat, numbered
  2..10").
- actions: UNIQUE(hand_id, sequence), street CHECK (0..3),
  action_type in ('FOLD','CHECK','CALL','BET','RAISE','POST') with
  amount/semantics/post_type coupling, amount CHECK (amount IS NULL OR
  amount >= 0), composite FK (hand_id, seat_number) ->
  hand_players(hand_id, seat_number).
- cards: CHECK length=2, rank in 23456789TJQKA, suit in cdhs (case-sensitive);
  per-hand uniqueness of hole cards and board cards plus a schema-level
  mechanism (trigger or per-hand card ledger) rejecting the same card as hole
  AND board within one hand.
- decision_points: identity-only columns subset of
  {id, hand_id, action_id, created_at}.

Accepted-design deviation, documented explicitly (not silently): the design
brief asks for a "10-seat full-table (25 distinct cards)" commit test. Under
the canonical seat domain 2..10 the largest legal full ring seats 9 players,
so the maximum configuration is 9 x 2 hole cards + 5 board cards = 23 distinct
cards. Widening the seat domain to include seat 1 would contradict the
canonical domain model and the explicit "1 -> IntegrityError" bound, so the
full-ring test asserts the 23-card maximum instead of 25.
"""

import importlib
import os
import shutil
import sqlite3
import tempfile
import unittest
import uuid

from sqlalchemy import exc as sa_exc

RANKS = "23456789TJQKA"
SUITS = "cdhs"

# Deterministic card constants (no randomness; uuid is used for paths only).
ALL_CARDS = [rank + suit for rank in RANKS for suit in SUITS]
RING_HOLE_CARDS = ALL_CARDS[:18]  # 9 legal seats x 2 hole cards
RING_BOARD_CARDS = ALL_CARDS[18:23]  # flop 3 + turn 1 + river 1 = 5

FULL_RING_SEATS = tuple(range(2, 11))  # seats 2..10 inclusive = 9 legal seats

EXPECTED_TABLES = frozenset(
    {
        "raw_sources",
        "import_batches",
        "players",
        "player_aliases",
        "hands",
        "hand_players",
        "actions",
        "board_cards",
        "hole_cards",
        "uncalled_returns",
        "decision_points",
    }
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")


def _sql(raw):
    """Build a SQLAlchemy text() clause (lazy import keeps RED behavioral)."""
    from sqlalchemy import text

    return text(raw)


def _sqlite_url(db_path):
    """SQLite URL for an absolute Windows/POSIX path."""
    return "sqlite:///" + db_path.replace("\\", "/")


class SchemaContractBase(unittest.TestCase):
    """Shared lazy-import + per-test isolated SQLite database lifecycle."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pokertdd_")
        # Registered first so it runs last (LIFO): engine is disposed before
        # the directory tree is removed (Windows file locks).
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(
            self.tmpdir, "db-%s.sqlite3" % uuid.uuid4().hex
        )
        self.engine = None
        self.addCleanup(self._dispose_engine)
        # Lazy import: in Phase A every test errors with ModuleNotFoundError
        # ('No module named poker.db') individually.
        self.module = self._import_db()
        self.engine = self.module.session.create_engine(self.db_path)

    def _dispose_engine(self):
        if self.engine is not None:
            self.engine.dispose()

    def _import_db(self):
        module = importlib.import_module("poker.db")
        importlib.import_module("poker.db.models")
        importlib.import_module("poker.db.session")
        return module

    def _create_all(self, engine):
        models = importlib.import_module("poker.db.models")
        models.Base.metadata.create_all(engine)
        return models

    # -- integrity negative helper ------------------------------------
    def _assert_integrity_error(
        self, conn, statement, params=None, message_contains=None, context=""
    ):
        """Run raw SQL and require sqlite3.IntegrityError (now or at commit).

        Covers both immediate constraints (raised at execute) and deferred
        ones (raised at commit, e.g. the composite button_seat foreign key).
        """
        try:
            conn.execute(_sql(statement), params or {})
        except sa_exc.IntegrityError as err:
            self._verify_integrity_message(err, message_contains, context)
            self._safe_rollback(conn)
            return
        try:
            conn.commit()
        except sa_exc.IntegrityError as err:
            self._verify_integrity_message(err, message_contains, context)
            self._safe_rollback(conn)
            return
        self.fail(
            "expected IntegrityError for %s" % (context or statement)
        )

    def _safe_rollback(self, conn):
        """Roll the negative case back; the constraint may have already done it.

        RAISE(ROLLBACK, ...) triggers roll the active SQLite transaction back
        themselves, leaving no open transaction, so a plain conn.rollback()
        would raise ``OperationalError: cannot rollback - no transaction is
        active`` and mask the expected IntegrityError.
        """
        try:
            conn.rollback()
        except sa_exc.OperationalError:
            pass

    def _verify_integrity_message(self, err, message_contains, context):
        if message_contains is None:
            return
        cause = getattr(err, "orig", None) or err
        haystack = str(cause).lower()
        self.assertIn(
            message_contains.lower(),
            haystack,
            "IntegrityError message for %s did not mention %r: %s"
            % (context or "constraint", message_contains, cause),
        )

    # -- raw-SQL fixture helpers (no ORM; created_at columns are expected
    # -- to have database-level defaults) ------------------------------
    def _insert_raw_source(self, conn, name="raw-source"):
        conn.execute(
            _sql("INSERT INTO raw_sources(name) VALUES (:name)"), {"name": name}
        )
        return self._scalar(conn, "SELECT id FROM raw_sources WHERE name = :name", {"name": name})

    def _insert_batch(self, conn, raw_source_id):
        conn.execute(
            _sql(
                "INSERT INTO import_batches(raw_source_id) VALUES (:raw_source_id)"
            ),
            {"raw_source_id": raw_source_id},
        )
        return self._scalar(
            conn,
            "SELECT id FROM import_batches WHERE raw_source_id = :raw_source_id",
            {"raw_source_id": raw_source_id},
        )

    def _insert_player(self, conn, name):
        conn.execute(
            _sql("INSERT INTO players(name) VALUES (:name)"), {"name": name}
        )
        return self._scalar(conn, "SELECT id FROM players WHERE name = :name", {"name": name})

    def _insert_alias(self, conn, player_id, alias):
        conn.execute(
            _sql("INSERT INTO player_aliases(player_id, alias) VALUES (:player_id, :alias)"),
            {"player_id": player_id, "alias": alias},
        )

    def _insert_hand(
        self,
        conn,
        raw_source_id,
        import_batch_id,
        *,
        table_name="T-TEST",
        hand_number="HH-0001",
        parser_version="v1",
        content_sha256=None,
        small_blind=1,
        big_blind=2,
        ante=0,
        button_seat=2,
    ):
        if content_sha256 is None:
            content_sha256 = "sha256-%s-%s" % (table_name, hand_number)
        conn.execute(
            _sql(
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:raw_source_id,"
                " :import_batch_id, :table_name, :hand_number, :parser_version,"
                " :content_sha256, :small_blind, :big_blind, :ante, :button_seat)"
            ),
            {
                "raw_source_id": raw_source_id,
                "import_batch_id": import_batch_id,
                "table_name": table_name,
                "hand_number": hand_number,
                "parser_version": parser_version,
                "content_sha256": content_sha256,
                "small_blind": small_blind,
                "big_blind": big_blind,
                "ante": ante,
                "button_seat": button_seat,
            },
        )
        return self._scalar(
            conn,
            "SELECT id FROM hands WHERE table_name = :table_name"
            " AND hand_number = :hand_number AND parser_version = :parser_version"
            " ORDER BY id DESC LIMIT 1",
            {
                "table_name": table_name,
                "hand_number": hand_number,
                "parser_version": parser_version,
            },
        )

    def _insert_hand_player(self, conn, hand_id, seat_number, player_id, stack=100):
        conn.execute(
            _sql(
                "INSERT INTO hand_players(hand_id, seat_number, player_id, stack)"
                " VALUES (:hand_id, :seat_number, :player_id, :stack)"
            ),
            {
                "hand_id": hand_id,
                "seat_number": seat_number,
                "player_id": player_id,
                "stack": stack,
            },
        )

    def _insert_action(
        self,
        conn,
        hand_id,
        seat_number,
        player_id,
        sequence,
        street,
        action_type,
        amount=None,
        semantics=None,
        post_type=None,
    ):
        conn.execute(
            _sql(
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, :seat_number, :player_id, :sequence, :street,"
                " :action_type, :amount, :semantics, :post_type)"
            ),
            {
                "hand_id": hand_id,
                "seat_number": seat_number,
                "player_id": player_id,
                "sequence": sequence,
                "street": street,
                "action_type": action_type,
                "amount": amount,
                "semantics": semantics,
                "post_type": post_type,
            },
        )

    def _insert_board_card(self, conn, hand_id, card, street=1, position=1):
        conn.execute(
            _sql(
                "INSERT INTO board_cards(hand_id, card, street, position) VALUES"
                " (:hand_id, :card, :street, :position)"
            ),
            {"hand_id": hand_id, "card": card, "street": street, "position": position},
        )

    def _insert_hole_card(self, conn, hand_id, seat_number, card):
        conn.execute(
            _sql(
                "INSERT INTO hole_cards(hand_id, seat_number, card) VALUES"
                " (:hand_id, :seat_number, :card)"
            ),
            {"hand_id": hand_id, "seat_number": seat_number, "card": card},
        )

    def _insert_uncalled_return(self, conn, hand_id, player_id, amount):
        conn.execute(
            _sql(
                "INSERT INTO uncalled_returns(hand_id, player_id, amount) VALUES"
                " (:hand_id, :player_id, :amount)"
            ),
            {"hand_id": hand_id, "player_id": player_id, "amount": amount},
        )

    def _insert_decision_point(self, conn, hand_id, action_id):
        conn.execute(
            _sql(
                "INSERT INTO decision_points(hand_id, action_id) VALUES"
                " (:hand_id, :action_id)"
            ),
            {"hand_id": hand_id, "action_id": action_id},
        )

    def _seed_table(self, conn, *, hand_number="HH-0001", seats=(2, 3),
                    table_name="T-TEST"):
        """Committed raw_source + batch + players + hand + hand_players."""
        raw_id = self._insert_raw_source(conn)
        batch_id = self._insert_batch(conn, raw_id)
        player_ids = [self._insert_player(conn, "player-seat-%d" % seat) for seat in seats]
        hand_id = self._insert_hand(
            conn,
            raw_id,
            batch_id,
            table_name=table_name,
            hand_number=hand_number,
            button_seat=seats[0],
        )
        for seat, player_id in zip(seats, player_ids):
            self._insert_hand_player(conn, hand_id, seat, player_id, stack=100)
        conn.commit()
        return {
            "raw_source_id": raw_id,
            "import_batch_id": batch_id,
            "player_ids": player_ids,
            "hand_id": hand_id,
            "seats": tuple(seats),
        }

    # -- read helpers ---------------------------------------------------
    def _scalar(self, conn, statement, params=None):
        return conn.execute(_sql(statement), params or {}).scalar()

    def _table_names(self, conn):
        return {
            row[0]
            for row in conn.execute(
                _sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    " AND name NOT LIKE 'sqlite_%'"
                )
            )
        }

    def _create_table_sql(self, conn, table):
        row = conn.execute(
            _sql(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table"
            ),
            {"table": table},
        ).fetchone()
        self.assertIsNotNone(row, "table %s not found" % table)
        return row[0]

    def _pragma_rows(self, conn, pragma):
        return sorted(tuple(row) for row in conn.execute(_sql(pragma)))

    def _sqlite_master_tuples(self, engine):
        with engine.connect() as conn:
            rows = conn.execute(
                _sql(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master"
                    " WHERE name NOT LIKE 'sqlite_%'"
                )
            ).fetchall()
        return sorted((row[0], row[1], row[2], row[3]) for row in rows)

    def _alembic_upgrade_head(self, db_path):
        """Run `alembic upgrade head` headless against an arbitrary path."""
        from alembic import command
        from alembic.config import Config

        config = Config()
        config.set_main_option("script_location", MIGRATIONS_DIR)
        config.set_main_option("sqlalchemy.url", _sqlite_url(db_path))
        config.set_main_option(
            "prepend_sys_path", os.path.join(REPO_ROOT, "src")
        )
        command.upgrade(config, "head")


class TestDbModuleExists(SchemaContractBase):
    def test_db_module_importable(self):
        db = importlib.import_module("poker.db")
        models = importlib.import_module("poker.db.models")
        session = importlib.import_module("poker.db.session")
        self.assertIsNotNone(db)
        self.assertIsNotNone(models)
        self.assertIsNotNone(session)


class TestSchemaRecreation(SchemaContractBase):
    def test_all_planned_tables_exist(self):
        self._create_all(self.engine)
        with self.engine.connect() as conn:
            found = self._table_names(conn)
        missing = EXPECTED_TABLES - found
        self.assertEqual(
            missing, set(), "planned tables missing from schema: %s" % sorted(missing)
        )

    def test_two_creations_deterministic(self):
        self._create_all(self.engine)
        second_path = os.path.join(
            self.tmpdir, "db-%s.sqlite3" % uuid.uuid4().hex
        )
        second_engine = self.module.session.create_engine(second_path)
        self.addCleanup(second_engine.dispose)
        self._create_all(second_engine)
        rows_first = self._sqlite_master_tuples(self.engine)
        rows_second = self._sqlite_master_tuples(second_engine)
        self.assertEqual(
            rows_first,
            rows_second,
            "schema recreation must be deterministic (type,name,tbl_name,sql)",
        )

    def test_alembic_upgrade_matches_create_all(self):
        # AC1-STRONGER oracle: kept active on purpose (no skip); the Alembic
        # environment must run headless with only sqlalchemy.url provided.
        upgraded_path = os.path.join(
            self.tmpdir, "db-%s.sqlite3" % uuid.uuid4().hex
        )
        self._alembic_upgrade_head(upgraded_path)
        upgraded_engine = self.module.session.create_engine(upgraded_path)
        self.addCleanup(upgraded_engine.dispose)
        self._create_all(self.engine)

        with upgraded_engine.connect() as conn_a, self.engine.connect() as conn_b:
            tables_a = self._table_names(conn_a)
            tables_b = self._table_names(conn_b)
            # Precondition: identical planned-table sets in both databases.
            self.assertEqual(
                sorted(tables_a & EXPECTED_TABLES),
                sorted(tables_b & EXPECTED_TABLES),
                "planned table sets differ between alembic and create_all",
            )
            for table in sorted(EXPECTED_TABLES):
                self.assertIn(table, tables_a, "alembic DB missing table %s" % table)
                self.assertIn(table, tables_b, "create_all DB missing table %s" % table)
                info_a = self._pragma_rows(conn_a, "PRAGMA table_info(%s)" % table)
                info_b = self._pragma_rows(conn_b, "PRAGMA table_info(%s)" % table)
                self.assertEqual(
                    info_a,
                    info_b,
                    "PRAGMA table_info mismatch for %s" % table,
                )
                fks_a = self._pragma_rows(conn_a, "PRAGMA foreign_key_list(%s)" % table)
                fks_b = self._pragma_rows(conn_b, "PRAGMA foreign_key_list(%s)" % table)
                self.assertEqual(
                    fks_a,
                    fks_b,
                    "PRAGMA foreign_key_list mismatch for %s" % table,
                )


class TestForeignKeyProvenance(SchemaContractBase):
    def test_hand_raw_source_fk_negative(self):
        with self.engine.connect() as conn:
            self._create_all(self.engine)
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (999999, :import_batch_id,"
                " 'T-NEG', 'HH-NEG-1', 'v1', 'sha256-neg-1', 1, 2, 0, 2)",
                {"import_batch_id": batch_id},
                message_contains="foreign key",
                context="hands.raw_source_id -> raw_sources.id",
            )

    def test_hand_player_hand_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO hand_players(hand_id, seat_number, player_id, stack)"
                " VALUES (999999, 2, :player_id, 100)",
                {"player_id": seeded["player_ids"][0]},
                message_contains="foreign key",
                context="hand_players.hand_id -> hands.id",
            )

    def test_hand_player_player_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO hand_players(hand_id, seat_number, player_id, stack)"
                " VALUES (:hand_id, 4, 999999, 100)",
                {"hand_id": seeded["hand_id"]},
                message_contains="foreign key",
                context="hand_players.player_id -> players.id",
            )

    def test_action_hand_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (999999, 2, :player_id, 0, 0, 'CALL', 1, 'total_commitment', NULL)",
                {"player_id": seeded["player_ids"][0]},
                message_contains="foreign key",
                context="actions.hand_id -> hands.id",
            )

    def test_action_player_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, 999999, 0, 0, 'CALL', 1, 'total_commitment', NULL)",
                {"hand_id": seeded["hand_id"]},
                message_contains="foreign key",
                context="actions.player_id -> players.id",
            )

    def test_action_hand_player_seat_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)  # seats 2 and 3 only
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 7, :player_id, 0, 0, 'CALL', 1, 'total_commitment', NULL)",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="foreign key",
                context="actions(hand_id, seat_number) -> hand_players(hand_id, seat_number)",
            )

    def test_board_cards_hand_fk_negative(self):
        with self.engine.connect() as conn:
            self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO board_cards(hand_id, card, street, position) VALUES"
                " (999999, 'Ah', 1, 1)",
                message_contains="foreign key",
                context="board_cards.hand_id -> hands.id",
            )

    def test_hole_cards_seat_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)  # seats 2 and 3 only
            self._assert_integrity_error(
                conn,
                "INSERT INTO hole_cards(hand_id, seat_number, card) VALUES"
                " (:hand_id, 7, 'Ah')",
                {"hand_id": seeded["hand_id"]},
                message_contains="foreign key",
                context="hole_cards(hand_id, seat_number) -> hand_players(hand_id, seat_number)",
            )

    def test_button_seat_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, seats=(2, 3))  # button at seat 2
            self._assert_integrity_error(
                conn,
                "UPDATE hands SET button_seat = 7 WHERE id = :hand_id",
                {"hand_id": seeded["hand_id"]},
                message_contains="foreign key",
                context="hands(id, button_seat) -> hand_players(hand_id, seat_number)",
            )

    def test_uncalled_returns_hand_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO uncalled_returns(hand_id, player_id, amount) VALUES"
                " (999999, :player_id, 1)",
                {"player_id": seeded["player_ids"][0]},
                message_contains="foreign key",
                context="uncalled_returns.hand_id -> hands.id",
            )

    def test_uncalled_returns_player_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO uncalled_returns(hand_id, player_id, amount) VALUES"
                " (:hand_id, 999999, 1)",
                {"hand_id": seeded["hand_id"]},
                message_contains="foreign key",
                context="uncalled_returns.player_id -> players.id",
            )

    def test_decision_point_hand_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO decision_points(hand_id, action_id) VALUES"
                " (999999, NULL)",
                message_contains="foreign key",
                context="decision_points.hand_id -> hands.id",
            )

    def test_decision_point_action_fk_negative(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO decision_points(hand_id, action_id) VALUES"
                " (:hand_id, 999999)",
                {"hand_id": seeded["hand_id"]},
                message_contains="foreign key",
                context="decision_points.action_id -> actions.id",
            )

    def test_action_attributes_wrong_player_rejected(self):
        # Provenance: an action at (hand_id, seat_number) must attribute to the
        # player actually seated there (composite provenance FK, not just
        # "seat exists" + "player exists").
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)  # seats 2 and 3
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, :player_id, 0, 0, 'CALL', 1,"
                " 'total_commitment', NULL)",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][1]},
                message_contains="foreign key",
                context="actions(hand_id, seat_number, player_id)"
                " -> hand_players(hand_id, seat_number, player_id)",
            )

    def test_full_hand_single_transaction_commits(self):
        with self.engine.connect() as conn:
            self._create_all(self.engine)
            raw_id = self._insert_raw_source(conn, "txn-source")
            batch_id = self._insert_batch(conn, raw_id)
            alice = self._insert_player(conn, "Alice")
            bob = self._insert_player(conn, "Bob")
            hand_id = self._insert_hand(
                conn,
                raw_id,
                batch_id,
                table_name="T-TXN",
                hand_number="HH-TXN-001",
                button_seat=2,
            )
            self._insert_hand_player(conn, hand_id, 2, alice, stack=100)
            self._insert_hand_player(conn, hand_id, 3, bob, stack=100)
            self._insert_action(
                conn, hand_id, 2, alice, sequence=0, street=0,
                action_type="POST", amount=1, post_type="sb",
            )
            self._insert_action(
                conn, hand_id, 3, bob, sequence=1, street=0,
                action_type="POST", amount=2, post_type="bb",
            )
            self._insert_action(
                conn, hand_id, 2, alice, sequence=2, street=0,
                action_type="CALL", amount=2, semantics="total_commitment",
            )
            self._insert_hole_card(conn, hand_id, 2, "As")
            self._insert_hole_card(conn, hand_id, 2, "Ks")
            self._insert_hole_card(conn, hand_id, 3, "Qd")
            self._insert_hole_card(conn, hand_id, 3, "Jd")
            self._insert_board_card(conn, hand_id, "2h", street=1, position=1)
            self._insert_board_card(conn, hand_id, "7d", street=1, position=2)
            self._insert_board_card(conn, hand_id, "9c", street=1, position=3)
            self._insert_board_card(conn, hand_id, "Th", street=2, position=1)
            self._insert_board_card(conn, hand_id, "Ac", street=3, position=1)
            conn.commit()
        with self.engine.connect() as conn:
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM hand_players WHERE hand_id = :h", {"h": hand_id}), 2
            )
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM actions WHERE hand_id = :h", {"h": hand_id}), 3
            )
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM hole_cards WHERE hand_id = :h", {"h": hand_id}), 4
            )
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM board_cards WHERE hand_id = :h", {"h": hand_id}), 5
            )


class TestImportBatchLineageFks(SchemaContractBase):
    """Fail-to-pass negatives for the batch-lineage foreign keys (audit finding)."""

    def test_import_batches_raw_source_fk_negative(self):
        with self.engine.connect() as conn:
            self._assert_integrity_error(
                conn,
                "INSERT INTO import_batches(raw_source_id) VALUES (999999)",
                message_contains="foreign key",
                context="import_batches.raw_source_id -> raw_sources.id",
            )

    def test_hands_import_batch_fk_negative(self):
        # hands.import_batch_id -> import_batches.id negative, checked via an
        # UPDATE on a fully valid hand: an INSERT of an orphan hand is
        # confounded by the deferred composite hands(id, button_seat) FK,
        # which rejects ANY hand without seated hand_players at commit even
        # when the batch-lineage FK is absent, so only this form has a
        # mutually-exclusive mutation-check outcome.
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, hand_number="HH-BATCH-NEG")
            self._assert_integrity_error(
                conn,
                "UPDATE hands SET import_batch_id = 999999 WHERE id = :hand_id",
                {"hand_id": seeded["hand_id"]},
                message_contains="foreign key",
                context="hands.import_batch_id -> import_batches.id",
            )


class TestChipIntegerStorage(SchemaContractBase):
    CHIP_TABLES = ("hands", "hand_players", "actions", "uncalled_returns")

    def test_chip_columns_are_integer_in_ddl(self):
        self._create_all(self.engine)
        with self.engine.connect() as conn:
            for table in self.CHIP_TABLES:
                ddl = (self._create_table_sql(conn, table) or "").upper()
                with self.subTest(table=table):
                    self.assertIn("INTEGER", ddl, "%s DDL must use INTEGER" % table)
                    self.assertNotIn("REAL", ddl, "%s DDL must not use REAL" % table)
                    self.assertNotIn("FLOAT", ddl, "%s DDL must not use FLOAT" % table)
                    self.assertNotIn("DOUBLE", ddl, "%s DDL must not use DOUBLE" % table)

    def test_action_amount_stored_as_integer(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            hand_id = seeded["hand_id"]
            self._insert_action(
                conn, hand_id, 2, seeded["player_ids"][0], sequence=0, street=0,
                action_type="CALL", amount=0, semantics="total_commitment",
            )
            self._insert_action(
                conn, hand_id, 3, seeded["player_ids"][1], sequence=1, street=0,
                action_type="BET", amount=100, semantics="chips_added",
            )
            conn.commit()
            self.assertEqual(
                self._scalar(
                    conn,
                    "SELECT typeof(amount) FROM actions WHERE hand_id = :h AND sequence = 0",
                    {"h": hand_id},
                ),
                "integer",
            )
            self.assertEqual(
                self._scalar(
                    conn,
                    "SELECT typeof(amount) FROM actions WHERE hand_id = :h AND sequence = 1",
                    {"h": hand_id},
                ),
                "integer",
            )

    def test_uncalled_return_amount_stored_as_integer(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._insert_uncalled_return(conn, seeded["hand_id"], seeded["player_ids"][0], 0)
            self._insert_uncalled_return(conn, seeded["hand_id"], seeded["player_ids"][1], 100)
            conn.commit()
            types = [
                row[0]
                for row in conn.execute(_sql("SELECT typeof(amount) FROM uncalled_returns"))
            ]
            self.assertEqual(sorted(types), ["integer", "integer"])

    def test_hand_player_stack_stored_as_integer(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            extra_player = self._insert_player(conn, "stack-zero-player")
            self._insert_hand_player(conn, seeded["hand_id"], 4, extra_player, stack=0)
            conn.commit()
            stacks = [
                row[0] for row in conn.execute(_sql("SELECT typeof(stack) FROM hand_players"))
            ]
            self.assertEqual(stacks, ["integer", "integer", "integer"])

    def test_blinds_all_zero_stored_as_integer_zero(self):
        with self.engine.connect() as conn:
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            player_id = self._insert_player(conn, "zero-blind-player")
            hand_id = self._insert_hand(
                conn, raw_id, batch_id, table_name="T-ZERO", hand_number="HH-ZERO",
                small_blind=0.0, big_blind=0.0, ante=0.0, button_seat=2,
            )
            self._insert_hand_player(conn, hand_id, 2, player_id, stack=100)
            self._insert_hand_player(conn, hand_id, 3, self._insert_player(conn, "zero-blind-player-2"), stack=100)
            conn.commit()
            row = conn.execute(
                _sql(
                    "SELECT typeof(small_blind), small_blind, typeof(big_blind),"
                    " big_blind, typeof(ante), ante FROM hands WHERE id = :h"
                ),
                {"h": hand_id},
            ).fetchone()
        self.assertEqual(row[0], "integer")
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], "integer")
        self.assertEqual(row[3], 0)
        self.assertEqual(row[4], "integer")
        self.assertEqual(row[5], 0)

    def test_negative_action_amount_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, :player_id, 0, 0, 'CALL', -1, 'total_commitment', NULL)",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="check",
                context="actions.amount >= 0",
            )

    def test_negative_uncalled_return_amount_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO uncalled_returns(hand_id, player_id, amount) VALUES"
                " (:hand_id, :player_id, -1)",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="check",
                context="uncalled_returns.amount >= 0",
            )

    def test_zero_amount_is_legal(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            hand_id = seeded["hand_id"]
            self._insert_action(
                conn, hand_id, 2, seeded["player_ids"][0], sequence=0, street=0,
                action_type="CALL", amount=0, semantics="total_commitment",
            )
            self._insert_uncalled_return(conn, hand_id, seeded["player_ids"][0], 0)
            conn.commit()
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM actions WHERE amount = 0"), 1)
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM uncalled_returns WHERE amount = 0"), 1)


class TestDedupAuditability(SchemaContractBase):
    def test_duplicate_natural_key_rejected(self):
        with self.engine.connect() as conn:
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            player_id = self._insert_player(conn, "dedup-player")
            hand_id = self._insert_hand(
                conn, raw_id, batch_id, table_name="T-DUP", hand_number="HH-DUP-1",
                parser_version="v1", content_sha256="sha256-dup-a", button_seat=2,
            )
            self._insert_hand_player(conn, hand_id, 2, player_id, stack=100)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:raw_source_id,"
                " :import_batch_id, 'T-DUP', 'HH-DUP-1', 'v1', 'sha256-dup-other',"
                " 1, 2, 0, 2)",
                {"raw_source_id": raw_id, "import_batch_id": batch_id},
                message_contains="unique",
                context="UNIQUE(raw_source_id, table_name, hand_number, parser_version)",
            )

    def test_same_natural_key_different_raw_source_allowed(self):
        with self.engine.connect() as conn:
            raw_a = self._insert_raw_source(conn, "source-a")
            raw_b = self._insert_raw_source(conn, "source-b")
            batch_a = self._insert_batch(conn, raw_a)
            batch_b = self._insert_batch(conn, raw_b)
            player_id = self._insert_player(conn, "cross-source-player")
            hand_a = self._insert_hand(
                conn, raw_a, batch_a, table_name="T-X", hand_number="HH-X-1",
                parser_version="v1", content_sha256="sha256-x-a", button_seat=2,
            )
            self._insert_hand_player(conn, hand_a, 2, player_id, stack=100)
            hand_b = self._insert_hand(
                conn, raw_b, batch_b, table_name="T-X", hand_number="HH-X-1",
                parser_version="v1", content_sha256="sha256-x-b", button_seat=2,
            )
            self._insert_hand_player(conn, hand_b, 2, player_id, stack=100)
            conn.commit()
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM hands"), 2)

    def test_duplicate_content_hash_same_parser_rejected(self):
        with self.engine.connect() as conn:
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            player_id = self._insert_player(conn, "hash-player")
            hand_id = self._insert_hand(
                conn, raw_id, batch_id, table_name="T-HASH", hand_number="HH-HASH-1",
                parser_version="v1", content_sha256="sha256-same", button_seat=2,
            )
            self._insert_hand_player(conn, hand_id, 2, player_id, stack=100)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:raw_source_id,"
                " :import_batch_id, 'T-HASH', 'HH-HASH-2', 'v1', 'sha256-same',"
                " 1, 2, 0, 2)",
                {"raw_source_id": raw_id, "import_batch_id": batch_id},
                message_contains="unique",
                context="UNIQUE(content_sha256, parser_version)",
            )

    def test_same_content_hash_different_parser_allowed(self):
        with self.engine.connect() as conn:
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            player_id = self._insert_player(conn, "reparse-player")
            hand_v1 = self._insert_hand(
                conn, raw_id, batch_id, table_name="T-RE", hand_number="HH-RE-1",
                parser_version="v1", content_sha256="sha256-re", button_seat=2,
            )
            self._insert_hand_player(conn, hand_v1, 2, player_id, stack=100)
            hand_v2 = self._insert_hand(
                conn, raw_id, batch_id, table_name="T-RE", hand_number="HH-RE-2",
                parser_version="v2", content_sha256="sha256-re", button_seat=2,
            )
            self._insert_hand_player(conn, hand_v2, 2, player_id, stack=100)
            conn.commit()
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM hands"), 2)

    def test_content_sha256_not_null(self):
        with self.engine.connect() as conn:
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            self._assert_integrity_error(
                conn,
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:raw_source_id,"
                " :import_batch_id, 'T-NULL', 'HH-NULL-1', 'v1', NULL, 1, 2, 0, 2)",
                {"raw_source_id": raw_id, "import_batch_id": batch_id},
                message_contains="not null",
                context="hands.content_sha256 NOT NULL",
            )

    def test_parser_version_not_null(self):
        with self.engine.connect() as conn:
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            self._assert_integrity_error(
                conn,
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:raw_source_id,"
                " :import_batch_id, 'T-NULL', 'HH-NULL-2', NULL, 'sha256-null',"
                " 1, 2, 0, 2)",
                {"raw_source_id": raw_id, "import_batch_id": batch_id},
                message_contains="not null",
                context="hands.parser_version NOT NULL",
            )


class TestCardDomain(SchemaContractBase):
    def test_card_check_rejects_invalid_cards(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            for bad_card in ("tc", "10c", "AsX", "AX"):
                with self.subTest(card=bad_card):
                    self._assert_integrity_error(
                        conn,
                        "INSERT INTO hole_cards(hand_id, seat_number, card) VALUES"
                        " (:hand_id, 2, :card)",
                        {"hand_id": seeded["hand_id"], "card": bad_card},
                        message_contains="check",
                        context="card domain CHECK for %r" % bad_card,
                    )

    def test_card_check_accepts_canonical_ten(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._insert_hole_card(conn, seeded["hand_id"], 2, "Tc")
            conn.commit()
            self.assertEqual(
                self._scalar(
                    conn,
                    "SELECT COUNT(*) FROM hole_cards WHERE card = 'Tc'",
                ),
                1,
            )

    def test_duplicate_hole_card_per_hand_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._insert_hole_card(conn, seeded["hand_id"], 2, "As")
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO hole_cards(hand_id, seat_number, card) VALUES"
                " (:hand_id, 3, 'As')",
                {"hand_id": seeded["hand_id"]},
                message_contains="unique",
                context="UNIQUE(hand_id, card) on hole_cards",
            )

    def test_duplicate_board_card_per_hand_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._insert_board_card(conn, seeded["hand_id"], "As", street=1, position=1)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO board_cards(hand_id, card, street, position) VALUES"
                " (:hand_id, 'As', 1, 2)",
                {"hand_id": seeded["hand_id"]},
                message_contains="unique",
                context="UNIQUE(hand_id, card) on board_cards",
            )

    def test_same_card_as_hole_and_board_in_same_hand_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, hand_number="HH-CARD-DIR1")
            hand_id = seeded["hand_id"]
            self._insert_hole_card(conn, hand_id, 2, "Ks")
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO board_cards(hand_id, card, street, position) VALUES"
                " (:hand_id, 'Ks', 1, 1)",
                {"hand_id": hand_id},
                message_contains="check",
                context="hole-then-board same card in one hand (schema-level ledger)",
            )
        # Opposite direction: board first, then the same card as a hole card.
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, hand_number="HH-CARD-DIR2")
            hand_id = seeded["hand_id"]
            self._insert_board_card(conn, hand_id, "Qd", street=1, position=1)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO hole_cards(hand_id, seat_number, card) VALUES"
                " (:hand_id, 2, 'Qd')",
                {"hand_id": hand_id},
                message_contains="check",
                context="board-then-hole same card in one hand (schema-level ledger)",
            )

    def test_hole_card_update_not_on_board_rejected(self):
        # UPDATE-path closure: a hole card may not be edited to a card that is
        # already on the board of the same hand (RAISE(ROLLBACK) fires at
        # execute time).
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, hand_number="HH-UPD-H-1")
            hand_id = seeded["hand_id"]
            self._insert_hole_card(conn, hand_id, 2, "As")
            self._insert_board_card(conn, hand_id, "Qd", street=1, position=1)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "UPDATE hole_cards SET card = 'Qd'"
                " WHERE hand_id = :hand_id AND seat_number = 2",
                {"hand_id": hand_id},
                message_contains="check failed",
                context="hole card updated onto the board of the same hand",
            )

    def test_board_card_update_not_in_holes_rejected(self):
        # UPDATE-path closure, symmetric direction: a board card may not be
        # edited to a card already in the holes of the same hand.
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, hand_number="HH-UPD-B-1")
            hand_id = seeded["hand_id"]
            self._insert_hole_card(conn, hand_id, 2, "As")
            self._insert_board_card(conn, hand_id, "Qd", street=1, position=1)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "UPDATE board_cards SET card = 'As'"
                " WHERE hand_id = :hand_id AND street = 1 AND position = 1",
                {"hand_id": hand_id},
                message_contains="check failed",
                context="board card updated into the holes of the same hand",
            )

    def test_same_card_in_different_hands_allowed(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, hand_number="HH-SHARED-1")
            self._insert_hole_card(conn, seeded["hand_id"], 2, "As")
            other = self._seed_table(conn, hand_number="HH-SHARED-2")
            self._insert_hole_card(conn, other["hand_id"], 2, "As")
            conn.commit()
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM hole_cards WHERE card = 'As'"), 2
            )

    def test_full_ring_all_seats_distinct_cards_commit(self):
        # Maximum legal configuration under the canonical seat domain 2..10:
        # 9 seated players x 2 hole cards + 5 board cards = 23 distinct cards.
        self.assertEqual(len(FULL_RING_SEATS), 9)
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, hand_number="HH-FULL", seats=FULL_RING_SEATS)
            hand_id = seeded["hand_id"]
            used_cards = set()
            for offset, seat in enumerate(FULL_RING_SEATS):
                first = RING_HOLE_CARDS[offset * 2]
                second = RING_HOLE_CARDS[offset * 2 + 1]
                used_cards.update((first, second))
                self._insert_hole_card(conn, hand_id, seat, first)
                self._insert_hole_card(conn, hand_id, seat, second)
            for index, card in enumerate(RING_BOARD_CARDS):
                used_cards.add(card)
                street = 1 if index < 3 else (2 if index == 3 else 3)
                self._insert_board_card(conn, hand_id, card, street=street, position=1)
            conn.commit()
        self.assertEqual(len(used_cards), 23)
        with self.engine.connect() as conn:
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM hole_cards WHERE hand_id = :h", {"h": hand_id}), 18
            )
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM board_cards WHERE hand_id = :h", {"h": hand_id}), 5
            )


class TestSeatsAndButton(SchemaContractBase):
    def test_duplicate_seat_per_hand_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            other_player = self._insert_player(conn, "seat-clash-player")
            self._assert_integrity_error(
                conn,
                "INSERT INTO hand_players(hand_id, seat_number, player_id, stack)"
                " VALUES (:hand_id, 2, :player_id, 100)",
                {"hand_id": seeded["hand_id"], "player_id": other_player},
                message_contains="unique",
                context="UNIQUE(hand_id, seat_number) on hand_players",
            )

    def test_duplicate_player_per_hand_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO hand_players(hand_id, seat_number, player_id, stack)"
                " VALUES (:hand_id, 4, :player_id, 100)",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="unique",
                context="UNIQUE(hand_id, player_id) on hand_players",
            )

    def test_same_player_two_hands_allowed(self):
        with self.engine.connect() as conn:
            first = self._seed_table(conn, hand_number="HH-SP-1", seats=(2, 3))
            second = self._seed_table(conn, hand_number="HH-SP-2", seats=(2, 3))
            self.assertEqual(first["player_ids"][0], second["player_ids"][0])
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM hand_players"), 4)

    def test_seat_number_bounds_2_and_10_commit(self):
        with self.engine.connect() as conn:
            for seat in (2, 10):
                with self.subTest(seat=seat):
                    hand_number = "HH-SEAT-%d" % seat
                    seeded = self._seed_table(
                        conn, hand_number=hand_number, seats=(seat,)
                    )
                    self.assertEqual(
                        self._scalar(
                            conn,
                            "SELECT COUNT(*) FROM hand_players WHERE hand_id = :h",
                            {"h": seeded["hand_id"]},
                        ),
                        1,
                    )

    def test_seat_number_bounds_1_and_11_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            for bad_seat in (1, 11):
                with self.subTest(seat=bad_seat):
                    other_player = self._insert_player(
                        conn, "bad-seat-%d-player" % bad_seat
                    )
                    self._assert_integrity_error(
                        conn,
                        "INSERT INTO hand_players(hand_id, seat_number, player_id,"
                        " stack) VALUES (:hand_id, :seat, :player_id, 100)",
                        {
                            "hand_id": seeded["hand_id"],
                            "seat": bad_seat,
                            "player_id": other_player,
                        },
                        message_contains="check",
                        context="hand_players.seat_number BETWEEN 2 AND 10",
                    )

    def test_button_seat_not_null(self):
        with self.engine.connect() as conn:
            raw_id = self._insert_raw_source(conn)
            batch_id = self._insert_batch(conn, raw_id)
            self._assert_integrity_error(
                conn,
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:raw_source_id,"
                " :import_batch_id, 'T-BTN', 'HH-BTN-1', 'v1', 'sha256-btn',"
                " 1, 2, 0, NULL)",
                {"raw_source_id": raw_id, "import_batch_id": batch_id},
                message_contains="not null",
                context="hands.button_seat NOT NULL (exactly one button per hand)",
            )


class TestActionsAndStreets(SchemaContractBase):
    def _insert_call_action(self, conn, hand_id, seat, player_id, sequence,
                            amount=1, street=0):
        self._insert_action(
            conn, hand_id, seat, player_id, sequence=sequence, street=street,
            action_type="CALL", amount=amount, semantics="total_commitment",
        )

    def test_duplicate_sequence_per_hand_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            hand_id = seeded["hand_id"]
            self._insert_call_action(conn, hand_id, 2, seeded["player_ids"][0], sequence=0)
            conn.commit()
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 3, :player_id, 0, 0, 'BET', 5, 'chips_added', NULL)",
                {"hand_id": hand_id, "player_id": seeded["player_ids"][1]},
                message_contains="unique",
                context="UNIQUE(hand_id, sequence) on actions",
            )

    def test_same_sequence_different_hands_allowed(self):
        with self.engine.connect() as conn:
            first = self._seed_table(conn, hand_number="HH-SEQ-1")
            second = self._seed_table(conn, hand_number="HH-SEQ-2")
            self._insert_call_action(
                conn, first["hand_id"], 2, first["player_ids"][0], sequence=0
            )
            self._insert_call_action(
                conn, second["hand_id"], 2, second["player_ids"][0], sequence=0
            )
            conn.commit()
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM actions WHERE sequence = 0"), 2
            )

    def test_sequence_zero_is_legal(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._insert_call_action(conn, seeded["hand_id"], 2, seeded["player_ids"][0], sequence=0)
            conn.commit()
            self.assertEqual(
                self._scalar(
                    conn,
                    "SELECT COUNT(*) FROM actions WHERE hand_id = :h AND sequence = 0",
                    {"h": seeded["hand_id"]},
                ),
                1,
            )

    def test_street_bounds_0_and_3_commit(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            hand_id = seeded["hand_id"]
            self._insert_action(
                conn, hand_id, 2, seeded["player_ids"][0], sequence=0, street=0,
                action_type="POST", amount=1, post_type="sb",
            )
            self._insert_action(
                conn, hand_id, 3, seeded["player_ids"][1], sequence=1, street=3,
                action_type="BET", amount=10, semantics="chips_added",
            )
            conn.commit()
            streets = [
                row[0] for row in conn.execute(
                    _sql("SELECT street FROM actions WHERE hand_id = :h ORDER BY sequence"),
                    {"h": hand_id},
                )
            ]
            self.assertEqual(streets, [0, 3])

    def test_street_bounds_minus_1_and_4_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            for bad_street in (-1, 4):
                with self.subTest(street=bad_street):
                    self._assert_integrity_error(
                        conn,
                        "INSERT INTO actions(hand_id, seat_number, player_id,"
                        " sequence, street, action_type, amount, semantics,"
                        " post_type) VALUES (:hand_id, 2, :player_id, 0,"
                        " :street, 'BET', 5, 'chips_added', NULL)",
                        {
                            "hand_id": seeded["hand_id"],
                            "player_id": seeded["player_ids"][0],
                            "street": bad_street,
                        },
                        message_contains="check",
                        context="actions.street BETWEEN 0 AND 3",
                    )

    def test_fold_and_check_with_null_amount_and_semantics_commit(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            hand_id = seeded["hand_id"]
            self._insert_action(
                conn, hand_id, 2, seeded["player_ids"][0], sequence=0, street=0,
                action_type="FOLD",
            )
            self._insert_action(
                conn, hand_id, 3, seeded["player_ids"][1], sequence=1, street=0,
                action_type="CHECK",
            )
            conn.commit()
            self.assertEqual(
                self._scalar(conn, "SELECT COUNT(*) FROM actions WHERE amount IS NULL AND semantics IS NULL"), 2
            )

    def test_fold_with_non_null_amount_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, :player_id, 0, 0, 'FOLD', 5, NULL, NULL)",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="check",
                context="FOLD requires amount IS NULL",
            )

    def test_bet_and_call_with_null_amount_or_semantics_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            hand_id = seeded["hand_id"]
            player_a, player_b = seeded["player_ids"]
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, :player_id, 0, 0, 'CALL', NULL,"
                " 'total_commitment', NULL)",
                {"hand_id": hand_id, "player_id": player_a},
                message_contains="check",
                context="CALL requires amount IS NOT NULL",
            )
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 3, :player_id, 0, 0, 'BET', 5, NULL, NULL)",
                {"hand_id": hand_id, "player_id": player_b},
                message_contains="check",
                context="BET requires semantics IS NOT NULL",
            )

    def test_post_types_sb_bb_ante_commit(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn, seats=(2, 3, 4))
            hand_id = seeded["hand_id"]
            players = seeded["player_ids"]
            for sequence, (seat, player_id, post_type, amount) in enumerate(
                [(2, players[0], "sb", 1), (3, players[1], "bb", 2),
                 (4, players[2], "ante", 1)]
            ):
                self._insert_action(
                    conn, hand_id, seat, player_id, sequence=sequence, street=0,
                    action_type="POST", amount=amount, post_type=post_type,
                )
            conn.commit()
            post_types = [
                row[0]
                for row in conn.execute(
                    _sql(
                        "SELECT post_type FROM actions WHERE hand_id = :h"
                        " AND action_type = 'POST' ORDER BY sequence"
                    ),
                    {"h": hand_id},
                )
            ]
            self.assertEqual(post_types, ["sb", "bb", "ante"])

    def test_post_type_blind_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, :player_id, 0, 0, 'POST', 1, NULL, 'blind')",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="check",
                context="POST post_type must be sb/bb/ante",
            )

    def test_post_type_null_rejected(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, :player_id, 0, 0, 'POST', 1, NULL, NULL)",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="check",
                context="POST requires post_type",
            )

    def test_post_type_only_allowed_for_post(self):
        with self.engine.connect() as conn:
            seeded = self._seed_table(conn)
            self._assert_integrity_error(
                conn,
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:hand_id, 2, :player_id, 0, 0, 'CHECK', NULL, NULL, 'sb')",
                {"hand_id": seeded["hand_id"], "player_id": seeded["player_ids"][0]},
                message_contains="check",
                context="post_type only allowed when action_type = 'POST'",
            )


class TestWALAndTransactionBoundaries(SchemaContractBase):
    def test_factory_pragmas(self):
        with self.engine.connect() as conn:
            self.assertEqual(
                conn.execute(_sql("PRAGMA foreign_keys")).scalar(), 1
            )
            self.assertEqual(
                conn.execute(_sql("PRAGMA journal_mode")).scalar(), "wal"
            )
            busy_timeout = conn.execute(_sql("PRAGMA busy_timeout")).scalar()
            self.assertGreaterEqual(busy_timeout, 1)

    def test_wal_persists_across_reopen(self):
        self._create_all(self.engine)
        with self.engine.connect() as conn:
            self._insert_raw_source(conn)
            conn.commit()
        self.engine.dispose()
        self.engine = None
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0], "wal"
            )
        finally:
            connection.close()

    def test_uncommitted_insert_invisible_to_second_engine_until_commit(self):
        self._create_all(self.engine)
        second_engine = self.module.session.create_engine(self.db_path)
        self.addCleanup(second_engine.dispose)
        writer = self.engine.connect()
        reader = second_engine.connect()
        try:
            writer.begin()
            self._insert_raw_source(writer, "invisible-until-commit")
            self.assertEqual(
                reader.execute(_sql("SELECT COUNT(*) FROM raw_sources")).scalar(), 0
            )
            writer.commit()
        finally:
            reader.close()
            writer.close()
        fresh_reader = second_engine.connect()
        try:
            self.assertEqual(
                fresh_reader.execute(_sql("SELECT COUNT(*) FROM raw_sources")).scalar(), 1
            )
        finally:
            fresh_reader.close()

    def test_rollback_discards_rows(self):
        self._create_all(self.engine)
        conn = self.engine.connect()
        try:
            conn.begin()
            self._insert_raw_source(conn, "doomed-row")
            conn.rollback()
        finally:
            conn.close()
        fresh = self.engine.connect()
        try:
            self.assertEqual(
                fresh.execute(_sql("SELECT COUNT(*) FROM raw_sources")).scalar(), 0
            )
        finally:
            fresh.close()

    def test_wal_sidecar_exists_while_open(self):
        self._create_all(self.engine)
        with self.engine.connect() as conn:
            self._insert_raw_source(conn, "wal-sidecar-source")
            conn.commit()
        self.assertTrue(
            os.path.exists(self.db_path + "-wal"),
            "WAL sidecar file must exist while the database is open",
        )


class TestDecisionPointsIdentityOnly(SchemaContractBase):
    IDENTITY_COLUMNS = frozenset({"id", "hand_id", "action_id", "created_at"})
    REQUIRED_COLUMNS = frozenset({"id", "hand_id", "action_id"})

    def test_decision_point_columns_are_identity_only(self):
        self._create_all(self.engine)
        with self.engine.connect() as conn:
            names = {
                row[1]
                for row in conn.execute(_sql("PRAGMA table_info(decision_points)"))
            }
        self.assertLessEqual(names, self.IDENTITY_COLUMNS)
        self.assertGreaterEqual(names, self.REQUIRED_COLUMNS)

    def test_decision_point_columns_have_no_real_types(self):
        self._create_all(self.engine)
        with self.engine.connect() as conn:
            types = [
                row[2]
                for row in conn.execute(_sql("PRAGMA table_info(decision_points)"))
            ]
        for declared_type in types:
            self.assertFalse(
                declared_type
                and any(
                    keyword in declared_type.upper()
                    for keyword in ("REAL", "FLOAT", "DOUBLE")
                ),
                "decision_points must not use REAL/FLOAT/DOUBLE types: %s"
                % (declared_type,),
            )


class TestPlayersAndAliases(SchemaContractBase):
    def test_alias_player_fk_negative(self):
        with self.engine.connect() as conn:
            self._assert_integrity_error(
                conn,
                "INSERT INTO player_aliases(player_id, alias) VALUES (999999, 'alias')",
                message_contains="foreign key",
                context="player_aliases.player_id -> players.id",
            )

    def test_player_name_null_rejected(self):
        with self.engine.connect() as conn:
            self._assert_integrity_error(
                conn,
                "INSERT INTO players(name) VALUES (NULL)",
                message_contains="not null",
                context="players.name NOT NULL",
            )

    def test_player_name_empty_rejected(self):
        with self.engine.connect() as conn:
            self._assert_integrity_error(
                conn,
                "INSERT INTO players(name) VALUES ('')",
                message_contains="check",
                context="players.name must not be empty",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
