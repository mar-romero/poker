"""Behavior-defining tests for the derived-features materializer (R2, TDD).

Two-phase lazy-import RED design (mirrors tests/db/test_schema.py): there are
NO top-level imports of ``poker.features`` or ``poker.db``; every test reaches
the feature layer and the database layer through
``importlib.import_module`` calls inside ``setUp``. Phase A therefore fails per
test with ``ModuleNotFoundError: No module named 'poker.features'`` (zero
collection errors); Phase B fails behaviorally at ``NotImplementedError``.

Designer oracle (test-designer.json, task POKER-DATA-FEATURES-001):

* S01-S09: schema contract — alembic head = exactly 13 planned tables
  (11 raw + feature_decisions + feature_opportunities, the deliberate
  derived-features extension of PS-3), create_all-vs-alembic parity for both
  new tables INCLUDING the explicit-revision create path, FK/CHECK/UNIQUE/
  NOT-NULL negatives, downgrade -1 returns to the 11 raw tables with raw data
  intact, created_at server default.
* M01-M12: materialize semantics — exact pinned row content derived by hand
  from the planner vocabulary rules (positions/pot type/facing/initiative/
  sizing/board descriptor/permille effective stack), engine-derived snapshot
  correspondence, name-seat adapter, cards-optional, fail-closed edges.
* R01-R05: recompute — raw tables byte-identical, idempotence, version
  coexistence, chunked subsets, one-hand-per-transaction partial failure.
* D01-D02: determinism — byte-identical values across independent runs and
  chunk-vs-full composition equality.
* F01-F02: fail-closed — big_blind=0 ValueError naming the hand; replay
  errors propagate with zero rows for the failing hand.
* T01-T03: RED-discipline structure — no module-level poker imports, package
  marker present, setUp lazy-imports both layers.

Documented resolution notes (test-designer "unresolved" case: implementer
resolves from planner step 8 wording and documents, never invents vocabulary):

* effective_stack_milli_bb uses the planner-pinned formula
  ``chips * 1000 // big_blind`` (M09's illustrative "1001/100 -> 10000"
  corresponds to ``(chips // bb) * 1000``; the planner formula yields 10010 —
  planner wording is authoritative, flagged for review).
* Positions for N >= 4 follow ``m = N - 3`` interior seats (first 'UTG', last
  'CO', interior 'MP'); N=9 full ring therefore has FOUR 'MP' seats
  (UTG, MP, MP, MP, MP, CO), not the three sketched in M03's example.
* 'monotone-paired' board texture is unreachable (a duplicated rank requires a
  second suit; the card domain forbids identical cards) — covered generically
  via 'two-tone-paired' and 'rainbow-paired'.
* INITIATIVE: the planner rule is implemented verbatim, but under StateEngine
  strict replay the last same-street aggressor never faces another decision on
  that street (they are marked acted; only a LATER aggression by another
  player re-opens decisions and that player then owns "last aggression"), so
  every engine-legal decision row carries initiative 0 (verified empirically
  against the engine). The initiative=1 case is pinned at the pure-builder
  level with a synthetic sequence; integration rows are pinned to 0.
"""

import dataclasses
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
import uuid

from sqlalchemy import exc as sa_exc

INITIAL_REVISION = "a1c9f2b4e6d8"

RAW_TABLES = (
    "hands",
    "actions",
    "hand_players",
    "board_cards",
    "hole_cards",
    "uncalled_returns",
    "decision_points",
    "players",
    "player_aliases",
    "raw_sources",
    "import_batches",
)

# The 11 raw normalized tables plus the two deliberate derived-features
# extension tables (PS-3 extension pinned by planner step 12).
ALL_PLANNED_TABLES = frozenset(RAW_TABLES) | {
    "feature_decisions",
    "feature_opportunities",
}

DECISION_READ = (
    "hand_id, action_id, seat_number, position, initiative, pot_type,"
    " effective_stack_milli_bb, facing_action, sizing_bucket"
)
OPPORTUNITY_READ = (
    "hand_id, action_id, street, seat_number, legal_actions, board_descriptor"
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")


def _sql(raw):
    from sqlalchemy import text

    return text(raw)


def _sqlite_url(db_path):
    return "sqlite:///" + db_path.replace("\\", "/")


class FeaturesContractBase(unittest.TestCase):
    """Lazy-import + per-test isolated SQLite lifecycle (Windows-safe LIFO)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pokertdd_feat_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "db-%s.sqlite3" % uuid.uuid4().hex)
        self.engine = None
        self.addCleanup(self._dispose_engine)
        # Lazy imports: Phase A fails HERE per test with ModuleNotFoundError
        # ('No module named poker.features'); Phase B fails behaviorally at
        # NotImplementedError; GREEN provides the real modules.
        self.features = importlib.import_module("poker.features")
        self.schema = importlib.import_module("poker.features.schema")
        self.materialize = importlib.import_module("poker.features.materialize")
        self.db_models = importlib.import_module("poker.db.models")
        self.db_session = importlib.import_module("poker.db.session")
        self.state_module = importlib.import_module("poker.engine.state")
        self.engine = self.db_session.create_engine(self.db_path)

    def _dispose_engine(self):
        if self.engine is not None:
            self.engine.dispose()

    # -- schema/DML helpers (raw SQL, no ORM) ---------------------------
    def _scalar(self, conn, statement, params=None):
        return conn.execute(_sql(statement), params or {}).scalar()

    def _commit(self, conn):
        try:
            conn.commit()
        except sa_exc.OperationalError:
            pass

    def _assert_integrity_error(self, conn, statement, params=None, message_contains=None, context=""):
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
        self.fail("expected IntegrityError for %s" % (context or statement))

    def _safe_rollback(self, conn):
        try:
            conn.rollback()
        except sa_exc.OperationalError:
            pass

    def _verify_integrity_message(self, err, message_contains, context):
        if message_contains is None:
            return
        cause = getattr(err, "orig", None) or err
        self.assertIn(
            message_contains.lower(),
            str(cause).lower(),
            "IntegrityError message for %s did not mention %r: %s"
            % (context or "constraint", message_contains, cause),
        )

    def _build_history(
        self,
        conn,
        hand_number,
        seat_specs,
        actions,
        board=(),
        *,
        table_name="T-F",
        sb=5,
        bb=10,
        ante=0,
        button_seat=None,
    ):
        """Insert one normalized hand via raw SQL (no importer on main).

        seat_specs: tuple of (seat_number, name, stack); actions: tuples of
        (sequence, seat, action_type, amount, semantics, post_type, street);
        board: tuples of (street, card, position). Returns ids for oracles.
        """
        if button_seat is None:
            button_seat = seat_specs[0][0]
        conn.execute(_sql("INSERT INTO raw_sources(name) VALUES (:n)"), {"n": "src-" + hand_number})
        raw_id = self._scalar(conn, "SELECT MAX(id) FROM raw_sources")
        conn.execute(_sql("INSERT INTO import_batches(raw_source_id) VALUES (:r)"), {"r": raw_id})
        batch_id = self._scalar(conn, "SELECT MAX(id) FROM import_batches")
        player_ids = {}
        for seat, name, _stack in seat_specs:
            conn.execute(_sql("INSERT INTO players(name) VALUES (:n)"), {"n": name})
            player_ids[seat] = self._scalar(conn, "SELECT MAX(id) FROM players")
        conn.execute(
            _sql(
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:raw_source_id,"
                " :import_batch_id, :table_name, :hand_number, 'v1',"
                " :content_sha256, :small_blind, :big_blind, :ante, :button_seat)"
            ),
            {
                "raw_source_id": raw_id,
                "import_batch_id": batch_id,
                "table_name": table_name,
                "hand_number": hand_number,
                "content_sha256": "sha256-%s" % hand_number,
                "small_blind": sb,
                "big_blind": bb,
                "ante": ante,
                "button_seat": button_seat,
            },
        )
        hand_id = self._scalar(conn, "SELECT MAX(id) FROM hands")
        for seat, _name, stack in seat_specs:
            conn.execute(
                _sql(
                    "INSERT INTO hand_players(hand_id, seat_number, player_id, stack)"
                    " VALUES (:h, :s, :p, :st)"
                ),
                {"h": hand_id, "s": seat, "p": player_ids[seat], "st": stack},
            )
        action_ids = {}
        for seq, seat, kind, amount, semantics, post_type, street in actions:
            conn.execute(
                _sql(
                    "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                    " street, action_type, amount, semantics, post_type) VALUES"
                    " (:h, :s, :p, :seq, :street, :kind, :amount, :semantics, :post_type)"
                ),
                {
                    "h": hand_id,
                    "s": seat,
                    "p": player_ids[seat],
                    "seq": seq,
                    "street": street,
                    "kind": kind,
                    "amount": amount,
                    "semantics": semantics,
                    "post_type": post_type,
                },
            )
            action_ids[seq] = self._scalar(conn, "SELECT MAX(id) FROM actions")
        for street, card, position in board:
            conn.execute(
                _sql(
                    "INSERT INTO board_cards(hand_id, card, street, position)"
                    " VALUES (:h, :c, :st, :p)"
                ),
                {"h": hand_id, "c": card, "st": street, "p": position},
            )
        self._commit(conn)
        return {
            "hand_id": hand_id,
            "player_ids": player_ids,
            "action_ids": action_ids,
            "seats": [seat for seat, _name, _stack in seat_specs],
        }

    # -- fixtures (all engine-legal; verified against StateEngine) -------
    POSTS_3 = (
        (0, 3, "POST", 5, None, "sb", 0),
        (1, 4, "POST", 10, None, "bb", 0),
    )
    POSTS_HU = (
        (0, 2, "POST", 5, None, "sb", 0),
        (1, 3, "POST", 10, None, "bb", 0),
    )

    def _seed_main(self, conn, hand_number="HH-MAIN"):
        """F_MAIN: limp, flop bet/fold/re-raise, turn bet, terminal fold."""
        actions = self.POSTS_3 + (
            (2, 2, "CALL", 10, "total_commitment", None, 0),
            (3, 3, "CALL", 10, "total_commitment", None, 0),
            (4, 4, "CHECK", None, None, None, 0),
            (5, 3, "CHECK", None, None, None, 1),
            (6, 4, "BET", 10, "chips_added", None, 1),
            (7, 2, "FOLD", None, None, None, 1),
            (8, 3, "RAISE", 30, "total_commitment", None, 1),
            (9, 4, "CALL", 30, "total_commitment", None, 1),
            (10, 3, "BET", 10, "chips_added", None, 2),
            (11, 4, "FOLD", None, None, None, 2),
        )
        board = ((1, "2h", 1), (1, "7d", 2), (1, "9c", 3), (2, "Th", 1))
        return self._build_history(
            conn,
            hand_number,
            ((2, "Btn", 200), (3, "Sb", 200), (4, "Bb", 200)),
            actions,
            board,
        )

    def _seed_initiative(self, conn, hand_number="HH-INIT"):
        """F_INIT: flop bet/call/raise with the opener calling back in."""
        actions = self.POSTS_3 + (
            (2, 2, "CALL", 10, "total_commitment", None, 0),
            (3, 3, "CALL", 10, "total_commitment", None, 0),
            (4, 4, "CHECK", None, None, None, 0),
            (5, 3, "BET", 10, "chips_added", None, 1),
            (6, 4, "CALL", 10, "chips_added", None, 1),
            (7, 2, "RAISE", 30, "total_commitment", None, 1),
            (8, 3, "CALL", 30, "total_commitment", None, 1),
            (9, 4, "CALL", 30, "total_commitment", None, 1),
        )
        board = ((1, "2h", 1), (1, "7d", 2), (1, "9c", 3))
        return self._build_history(
            conn,
            hand_number,
            ((2, "Btn", 200), (3, "Sb", 200), (4, "Bb", 200)),
            actions,
            board,
        )

    def _seed_single_raised(self, conn, hand_number="HH-SR"):
        """F_SR: single-raised preflop, two flop checks, NO board rows."""
        actions = self.POSTS_3 + (
            (2, 2, "RAISE", 30, "total_commitment", None, 0),
            (3, 3, "CALL", 30, "total_commitment", None, 0),
            (4, 4, "CALL", 30, "total_commitment", None, 0),
            (5, 3, "CHECK", None, None, None, 1),
            (6, 4, "CHECK", None, None, None, 1),
        )
        return self._build_history(
            conn,
            hand_number,
            ((2, "Btn", 200), (3, "Sb", 200), (4, "Bb", 200)),
            actions,
        )

    def _seed_three_bet(self, conn, hand_number="HH-3B"):
        """F_3B: raise/raise/raise preflop then two cold calls."""
        actions = self.POSTS_3 + (
            (2, 2, "RAISE", 30, "total_commitment", None, 0),
            (3, 3, "RAISE", 60, "total_commitment", None, 0),
            (4, 4, "RAISE", 120, "total_commitment", None, 0),
            (5, 2, "CALL", 120, "total_commitment", None, 0),
            (6, 3, "CALL", 120, "total_commitment", None, 0),
        )
        return self._build_history(
            conn,
            hand_number,
            ((2, "Btn", 200), (3, "Sb", 200), (4, "Bb", 200)),
            actions,
        )

    def _seed_unopened(self, conn, hand_number="HH-UNOP"):
        """F_UNOP: heads-up posts then the button folds immediately."""
        actions = self.POSTS_HU + ((2, 2, "FOLD", None, None, None, 0),)
        return self._build_history(
            conn, hand_number, ((2, "HuBtn", 200), (3, "HuBb", 200)), actions
        )

    def _seed_hu_call_check(self, conn, hand_number, names=("A2", "A3")):
        """F_HU2-style: posts, button call, big-blind check (2 decisions)."""
        actions = self.POSTS_HU + (
            (2, 2, "CALL", 10, "total_commitment", None, 0),
            (3, 3, "CHECK", None, None, None, 0),
        )
        return self._build_history(
            conn,
            hand_number,
            ((2, names[0], 200), (3, names[1], 200)),
            actions,
        )

    def _seed_fold_around(self, conn, hand_number, seat_specs, action_specs):
        """Generic seats/folds/complete/check skeleton used for N=4/6/9."""
        actions = (
            (0, seat_specs[1][0], "POST", 5, None, "sb", 0),
            (1, seat_specs[2][0], "POST", 10, None, "bb", 0),
        ) + tuple(
            (seq, seat, kind, amount, semantics, post_type, 0)
            for seq, seat, kind, amount, semantics, post_type in action_specs
        )
        return self._build_history(conn, hand_number, seat_specs, actions, button_seat=seat_specs[0][0])

    def _seed_sizing(self, conn, hand_number, bet_amount, *, sb=1, bb=2, stacks=((200,), (200,))):
        """F_SIZEx: posts, limp call, check, flop bet of bet_amount, fold."""
        actions = (
            (0, 2, "POST", sb, None, "sb", 0),
            (1, 3, "POST", bb, None, "bb", 0),
            (2, 2, "CALL", bb, "total_commitment", None, 0),
            (3, 3, "CHECK", None, None, None, 0),
            (4, 2, "BET", bet_amount, "chips_added", None, 1),
            (5, 3, "FOLD", None, None, None, 1),
        )
        board = ((1, "2h", 1), (1, "3d", 2), (1, "4c", 3))
        return self._build_history(
            conn,
            hand_number,
            ((2, "SzBtn", stacks[0][0]), (3, "SzBb", stacks[1][0])),
            actions,
            board,
            sb=sb,
            bb=bb,
        )

    def _seed_all_in(self, conn, hand_number="HH-ALLIN"):
        """F_ALLIN: flop shove for exactly the remaining stack."""
        actions = self.POSTS_HU + (
            (2, 2, "CALL", 10, "total_commitment", None, 0),
            (3, 3, "CHECK", None, None, None, 0),
            (4, 2, "BET", 30, "chips_added", None, 1),
        )
        board = ((1, "2h", 1), (1, "3d", 2), (1, "4c", 3))
        return self._build_history(
            conn,
            hand_number,
            ((2, "AllBtn", 40), (3, "AllBb", 200)),
            actions,
            board,
        )

    def _seed_check_line(self, conn, hand_number, board):
        """F_BOARD-style: posts, limp/check preflop, check-around each street."""
        actions = self.POSTS_3 + (
            (2, 2, "CALL", 10, "total_commitment", None, 0),
            (3, 3, "CALL", 10, "total_commitment", None, 0),
            (4, 4, "CHECK", None, None, None, 0),
            (5, 3, "CHECK", None, None, None, 1),
            (6, 4, "CHECK", None, None, None, 1),
            (7, 2, "CHECK", None, None, None, 1),
            (8, 3, "CHECK", None, None, None, 2),
            (9, 4, "CHECK", None, None, None, 2),
            (10, 2, "CHECK", None, None, None, 2),
            (11, 3, "CHECK", None, None, None, 3),
            (12, 4, "CHECK", None, None, None, 3),
            (13, 2, "CHECK", None, None, None, 3),
        )
        return self._build_history(
            conn,
            hand_number,
            ((2, "Btn", 200), (3, "Sb", 200), (4, "Bb", 200)),
            actions,
            board,
        )

    def _seed_all_in_posts(self, conn, hand_number="HH-M11"):
        """F_M11: both players post themselves all-in (no voluntary actions)."""
        actions = (
            (0, 2, "POST", 5, None, "sb", 0),
            (1, 3, "POST", 10, None, "bb", 0),
        )
        return self._build_history(
            conn, hand_number, ((2, "ShortSb", 5), (3, "ShortBb", 10)), actions
        )

    def _seed_duplicate_names(self, conn, hand_number="HH-DUPNAME"):
        """F_DUPNAME: two distinct player rows sharing one name in one hand."""
        conn.execute(_sql("INSERT INTO raw_sources(name) VALUES (:n)"), {"n": "src-" + hand_number})
        raw_id = self._scalar(conn, "SELECT MAX(id) FROM raw_sources")
        conn.execute(_sql("INSERT INTO import_batches(raw_source_id) VALUES (:r)"), {"r": raw_id})
        batch_id = self._scalar(conn, "SELECT MAX(id) FROM import_batches")
        first = self._scalar(conn, "SELECT MAX(id) FROM players") or 0
        conn.execute(_sql("INSERT INTO players(name) VALUES ('Dup')"))
        player_a = self._scalar(conn, "SELECT MAX(id) FROM players")
        conn.execute(_sql("INSERT INTO players(name) VALUES ('Dup')"))
        player_b = self._scalar(conn, "SELECT MAX(id) FROM players")
        conn.execute(
            _sql(
                "INSERT INTO hands(raw_source_id, import_batch_id, table_name,"
                " hand_number, parser_version, content_sha256, small_blind,"
                " big_blind, ante, button_seat) VALUES (:r, :b, 'T-DUP',"
                " :hn, 'v1', :sha, 5, 10, 0, 2)"
            ),
            {"r": raw_id, "b": batch_id, "hn": hand_number, "sha": "sha256-" + hand_number},
        )
        hand_id = self._scalar(conn, "SELECT MAX(id) FROM hands")
        conn.execute(
            _sql(
                "INSERT INTO hand_players(hand_id, seat_number, player_id, stack)"
                " VALUES (:h, 2, :pa, 200), (:h, 3, :pb, 200)"
            ),
            {"h": hand_id, "pa": player_a, "pb": player_b},
        )
        conn.execute(
            _sql(
                "INSERT INTO actions(hand_id, seat_number, player_id, sequence,"
                " street, action_type, amount, semantics, post_type) VALUES"
                " (:h, 2, :pa, 0, 0, 'POST', 5, NULL, 'sb'),"
                " (:h, 3, :pb, 1, 0, 'POST', 10, NULL, 'bb')"
            ),
            {"h": hand_id, "pa": player_a, "pb": player_b},
        )
        self._commit(conn)
        return hand_id

    def _seed_wrong_turn(self, conn, hand_number):
        """F_F02: posts then the small blind acts out of turn (3 players)."""
        actions = self.POSTS_3 + ((2, 3, "FOLD", None, None, None, 0),)
        return self._build_history(
            conn,
            hand_number,
            ((2, "WtBtn", 200), (3, "WtSb", 200), (4, "WtBb", 200)),
            actions,
        )

    def _seed_bb_zero(self, conn, hand_number, *, with_bet):
        """F01/F07 edge: big_blind=0 hand (CHECK allows 0); optional empty-pot BET."""
        actions = [
            (0, 2, "POST", 0, None, "sb", 0),
            (1, 3, "POST", 0, None, "bb", 0),
        ]
        if with_bet:
            actions.append((2, 2, "BET", 1, "chips_added", None, 0))
        else:
            actions.append((2, 2, "FOLD", None, None, None, 0))
        return self._build_history(
            conn,
            hand_number,
            ((2, "ZbBtn", 200), (3, "ZbBb", 200)),
            tuple(actions),
            sb=0,
            bb=0,
        )

    # -- materialize + oracle readers ------------------------------------
    def _materialize(self, hand_ids=None, version=None):
        if version is None:
            version = self.schema.FEATURES_VERSION
        return self.materialize.materialize_features(
            self.engine, hand_ids=hand_ids, feature_version=version
        )

    def _decision_dump(self, conn, version):
        rows = conn.execute(
            _sql(
                "SELECT %s FROM feature_decisions WHERE feature_version = :v"
                " ORDER BY hand_id, action_id" % DECISION_READ
            ),
            {"v": version},
        ).fetchall()
        return [tuple(row) for row in rows]

    def _opp_dump(self, conn, version):
        rows = conn.execute(
            _sql(
                "SELECT %s FROM feature_opportunities WHERE feature_version = :v"
                " ORDER BY hand_id, street, seat_number" % OPPORTUNITY_READ
            ),
            {"v": version},
        ).fetchall()
        return [tuple(row) for row in rows]

    def _count(self, conn, table, version, hand_id=None):
        statement = "SELECT COUNT(*) FROM %s WHERE feature_version = :v" % table
        params = {"v": version}
        if hand_id is not None:
            statement += " AND hand_id = :h"
            params["h"] = hand_id
        return self._scalar(conn, statement, params)

    def _raw_digest(self, conn):
        digest = hashlib.sha256()
        for table in RAW_TABLES:
            rows = conn.execute(_sql("SELECT * FROM %s ORDER BY rowid" % table)).fetchall()
            payload = json.dumps(
                [[str(value) for value in row] for row in rows], sort_keys=True
            )
            digest.update(("%s|%s|" % (table, payload)).encode("utf-8"))
        return digest.hexdigest()

    def _extra_engine(self):
        path = os.path.join(self.tmpdir, "db-%s.sqlite3" % uuid.uuid4().hex)
        engine = self.db_session.create_engine(path)
        self.addCleanup(engine.dispose)
        return engine

    # -- alembic helpers (headless; mirrors tests/db/test_schema.py) -----
    def _alembic_config(self, db_path):
        from alembic import config as alembic_config

        cfg = alembic_config.Config()
        cfg.set_main_option("script_location", MIGRATIONS_DIR)
        cfg.set_main_option("sqlalchemy.url", _sqlite_url(db_path))
        cfg.set_main_option("prepend_sys_path", os.path.join(REPO_ROOT, "src"))
        return cfg

    def _alembic_upgrade_head(self, db_path):
        from alembic import command

        command.upgrade(self._alembic_config(db_path), "head")

    def _alembic_downgrade_minus_one(self, db_path):
        from alembic import command

        command.downgrade(self._alembic_config(db_path), "-1")

    def _sqlite_connect(self, path):
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        return conn

    def _sqlite_tables(self, path):
        conn = self._sqlite_connect(path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {row[0] for row in rows}

    def _pragma_dump(self, path, pragma):
        conn = self._sqlite_connect(path)
        return sorted(tuple(row) for row in conn.execute(pragma).fetchall())


# ---------------------------------------------------------------------------
# S01-S09: schema contract
# ---------------------------------------------------------------------------


class SchemaContractTests(FeaturesContractBase):
    def test_S01_upgrade_head_yields_13_tables(self):
        path = os.path.join(self.tmpdir, "alembic-%s.sqlite3" % uuid.uuid4().hex)
        self._alembic_upgrade_head(path)
        tables = self._sqlite_tables(path)
        self.assertEqual(tables - ALL_PLANNED_TABLES, {"alembic_version"})
        self.assertEqual(tables & ALL_PLANNED_TABLES, ALL_PLANNED_TABLES)
        self.assertIn("feature_decisions", tables)
        self.assertIn("feature_opportunities", tables)

    def test_S02_parity_two_new_tables(self):
        # Fresh upgrade: the initial revision delegates to the (grown) live
        # metadata, so both feature tables already exist; drop them and stamp
        # back so the explicit-revision create path (guarded op.create_table)
        # actually runs — the only way to compare it against create_all.
        path = os.path.join(self.tmpdir, "parity-%s.sqlite3" % uuid.uuid4().hex)
        self._alembic_upgrade_head(path)
        stamp = self._sqlite_connect(path)
        stamp.execute("DROP TABLE feature_opportunities")
        stamp.execute("DROP TABLE feature_decisions")
        stamp.execute(
            "UPDATE alembic_version SET version_num = ?", (INITIAL_REVISION,)
        )
        stamp.commit()
        stamp.close()
        self._alembic_upgrade_head(path)
        tables = self._sqlite_tables(path)
        self.assertIn("feature_decisions", tables)
        self.assertIn("feature_opportunities", tables)
        for table in ("feature_decisions", "feature_opportunities"):
            with self.subTest(table=table):
                self.assertEqual(
                    self._pragma_dump(path, "PRAGMA table_info(%s)" % table),
                    self._pragma_dump(self.db_path, "PRAGMA table_info(%s)" % table),
                    "table_info parity mismatch for %s" % table,
                )
                self.assertEqual(
                    self._pragma_dump(path, "PRAGMA foreign_key_list(%s)" % table),
                    self._pragma_dump(self.db_path, "PRAGMA foreign_key_list(%s)" % table),
                    "foreign_key_list parity mismatch for %s" % table,
                )
                self.assertEqual(
                    self._pragma_dump(path, "PRAGMA index_list(%s)" % table),
                    self._pragma_dump(self.db_path, "PRAGMA index_list(%s)" % table),
                    "index_list parity mismatch for %s" % table,
                )

    def _seed_fk_base(self, conn, hand_number):
        return self._build_history(
            conn,
            hand_number,
            ((2, "NegA", 200), (3, "NegB", 200)),
            self.POSTS_HU,
            button_seat=2,
        )

    def _decision_sql(self):
        return (
            "INSERT INTO feature_decisions(hand_id, action_id, feature_version,"
            " seat_number, position, initiative, pot_type,"
            " effective_stack_milli_bb, facing_action, sizing_bucket) VALUES"
            " (:hand_id, :action_id, :feature_version, :seat_number, :position,"
            " :initiative, :pot_type, :effective_stack_milli_bb, :facing_action,"
            " :sizing_bucket)"
        )

    def _decision_params(self, seeded, **overrides):
        params = {
            "hand_id": seeded["hand_id"],
            "action_id": seeded["action_ids"][1],
            "feature_version": "1.0.0",
            "seat_number": 2,
            "position": "BTN",
            "initiative": 0,
            "pot_type": "limped",
            "effective_stack_milli_bb": 20000,
            "facing_action": "unopened",
            "sizing_bucket": "check",
        }
        params.update(overrides)
        return params

    def _opportunity_params(self, seeded, **overrides):
        params = {
            "hand_id": seeded["hand_id"],
            "action_id": seeded["action_ids"][1],
            "feature_version": "1.0.0",
            "street": 0,
            "seat_number": 2,
            "legal_actions": "CALL,FOLD,RAISE",
            "board_descriptor": "",
        }
        params.update(overrides)
        return params

    def _opportunity_sql(self):
        return (
            "INSERT INTO feature_opportunities(hand_id, action_id, feature_version,"
            " street, seat_number, legal_actions, board_descriptor) VALUES"
            " (:hand_id, :action_id, :feature_version, :street, :seat_number,"
            " :legal_actions, :board_descriptor)"
        )

    def test_S03_fk_negative_decisions(self):
        with self.engine.connect() as conn:
            seeded = self._seed_fk_base(conn, "HH-S03")
            self._assert_integrity_error(
                conn,
                self._decision_sql(),
                self._decision_params(seeded, hand_id=999999),
                message_contains="foreign key",
                context="feature_decisions.hand_id -> hands.id",
            )
            self._assert_integrity_error(
                conn,
                self._decision_sql(),
                self._decision_params(seeded, action_id=999999),
                message_contains="foreign key",
                context="feature_decisions.action_id -> actions.id",
            )

    def test_S04_fk_negative_opportunities(self):
        with self.engine.connect() as conn:
            seeded = self._seed_fk_base(conn, "HH-S04")
            self._assert_integrity_error(
                conn,
                self._opportunity_sql(),
                self._opportunity_params(seeded, hand_id=999999),
                message_contains="foreign key",
                context="feature_opportunities.hand_id -> hands.id",
            )
            conn.execute(
                _sql(self._opportunity_sql()),
                self._opportunity_params(seeded, action_id=None),
            )
            self._commit(conn)
            self.assertEqual(
                self._count(conn, "feature_opportunities", "1.0.0", seeded["hand_id"]),
                1,
                "NULL action_id must be accepted on feature_opportunities",
            )
            self._assert_integrity_error(
                conn,
                self._decision_sql(),
                self._decision_params(seeded, action_id=None),
                message_contains="not null",
                context="feature_decisions.action_id NOT NULL",
            )

    def test_S05_seat_check_bounds(self):
        with self.engine.connect() as conn:
            seeded = self._seed_fk_base(conn, "HH-S05")
            for bad_seat in (1, 11):
                self._assert_integrity_error(
                    conn,
                    self._decision_sql(),
                    self._decision_params(seeded, seat_number=bad_seat),
                    message_contains="check",
                    context="feature_decisions seat CHECK (%d)" % bad_seat,
                )
                self._assert_integrity_error(
                    conn,
                    self._opportunity_sql(),
                    self._opportunity_params(seeded, seat_number=bad_seat, feature_version="s%d" % bad_seat),
                    message_contains="check",
                    context="feature_opportunities seat CHECK (%d)" % bad_seat,
                )
            for good_seat in (2, 10):
                version = "seat-%d" % good_seat
                conn.execute(
                    _sql(self._decision_sql()),
                    self._decision_params(seeded, seat_number=good_seat, feature_version=version),
                )
                conn.execute(
                    _sql(self._opportunity_sql()),
                    self._opportunity_params(seeded, seat_number=good_seat, feature_version=version),
                )
            self._commit(conn)
            self.assertEqual(self._count(conn, "feature_decisions", "seat-2"), 1)
            self.assertEqual(self._count(conn, "feature_decisions", "seat-10"), 1)
            self.assertEqual(self._count(conn, "feature_opportunities", "seat-2"), 1)
            self.assertEqual(self._count(conn, "feature_opportunities", "seat-10"), 1)

    def test_S06_street_check_bounds(self):
        with self.engine.connect() as conn:
            seeded = self._seed_fk_base(conn, "HH-S06")
            for bad_street in (4, -1):
                self._assert_integrity_error(
                    conn,
                    self._opportunity_sql(),
                    self._opportunity_params(seeded, street=bad_street),
                    message_contains="check",
                    context="feature_opportunities street CHECK (%d)" % bad_street,
                )
            self._assert_integrity_error(
                conn,
                self._decision_sql(),
                self._decision_params(seeded, initiative=2),
                message_contains="check",
                context="feature_decisions initiative CHECK (2)",
            )
            for good_street in (0, 3):
                version = "street-%d" % good_street
                conn.execute(
                    _sql(self._opportunity_sql()),
                    self._opportunity_params(seeded, street=good_street, feature_version=version),
                )
            self._commit(conn)
            self.assertEqual(self._count(conn, "feature_opportunities", "street-0"), 1)
            self.assertEqual(self._count(conn, "feature_opportunities", "street-3"), 1)

    def test_S07_unique_constraints(self):
        with self.engine.connect() as conn:
            seeded = self._seed_fk_base(conn, "HH-S07")
            conn.execute(_sql(self._decision_sql()), self._decision_params(seeded))
            self._commit(conn)
            self._assert_integrity_error(
                conn,
                self._decision_sql(),
                self._decision_params(seeded),
                message_contains="unique",
                context="UNIQUE(hand_id, action_id, feature_version) on feature_decisions",
            )
            conn.execute(
                _sql(self._opportunity_sql()), self._opportunity_params(seeded)
            )
            self._commit(conn)
            self._assert_integrity_error(
                conn,
                self._opportunity_sql(),
                self._opportunity_params(seeded),
                message_contains="unique",
                context="UNIQUE(hand_id, street, seat_number, feature_version)",
            )
            # Version-scoped identity: the same action under another version is fine.
            conn.execute(
                _sql(self._decision_sql()),
                self._decision_params(seeded, feature_version="2.0.0"),
            )
            self._commit(conn)
            self.assertEqual(
                self._count(conn, "feature_decisions", "2.0.0", seeded["hand_id"]), 1
            )

    def test_S08_downgrade_drops_features(self):
        path = os.path.join(self.tmpdir, "down-%s.sqlite3" % uuid.uuid4().hex)
        self._alembic_upgrade_head(path)
        # Seed raw rows so the round trip proves raw data is untouched.
        engine = self.db_session.create_engine(path)
        self.addCleanup(engine.dispose)
        with engine.connect() as conn:
            self._build_history(
                conn,
                "HH-DOWN",
                ((2, "DnA", 200), (3, "DnB", 200)),
                self.POSTS_HU,
                button_seat=2,
            )
        self._alembic_downgrade_minus_one(path)
        tables = self._sqlite_tables(path)
        self.assertNotIn("feature_decisions", tables)
        self.assertNotIn("feature_opportunities", tables)
        self.assertEqual(tables & ALL_PLANNED_TABLES, frozenset(RAW_TABLES))
        with engine.connect() as conn:
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM hands"), 1)
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM actions"), 2)
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM hand_players"), 2)

    def test_S09_not_null_and_defaults(self):
        with self.engine.connect() as conn:
            seeded = self._seed_fk_base(conn, "HH-S09")
            for column in ("legal_actions", "board_descriptor"):
                self._assert_integrity_error(
                    conn,
                    self._opportunity_sql(),
                    self._opportunity_params(seeded, **{column: None}),
                    message_contains="not null",
                    context="feature_opportunities.%s NOT NULL" % column,
                )
            for column in (
                "position",
                "initiative",
                "pot_type",
                "facing_action",
                "sizing_bucket",
            ):
                self._assert_integrity_error(
                    conn,
                    self._decision_sql(),
                    self._decision_params(seeded, **{column: None}),
                    message_contains="not null",
                    context="feature_decisions.%s NOT NULL" % column,
                )
            conn.execute(
                _sql(self._decision_sql()),
                self._decision_params(seeded, feature_version="default-check"),
            )
            conn.execute(
                _sql(self._opportunity_sql()),
                self._opportunity_params(seeded, feature_version="default-check"),
            )
            self._commit(conn)
            created = self._scalar(
                conn,
                "SELECT created_at FROM feature_decisions"
                " WHERE feature_version = 'default-check'",
            )
            self.assertIsNotNone(created)
            self.assertRegex(created, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
            opp_created = self._scalar(
                conn,
                "SELECT created_at FROM feature_opportunities"
                " WHERE feature_version = 'default-check'",
            )
            self.assertIsNotNone(opp_created)
            self.assertRegex(opp_created, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


# ---------------------------------------------------------------------------
# M01-M12: materialize semantics
# ---------------------------------------------------------------------------


class MaterializeContractTests(FeaturesContractBase):
    def test_M01_empty_db_zeros(self):
        result = self._materialize()
        self.assertIsInstance(result, self.materialize.MaterializeResult)
        self.assertEqual(
            (result.hands_processed, result.decision_rows, result.opportunity_rows),
            (0, 0, 0),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.hands_processed = 5
        with self.engine.connect() as conn:
            self.assertEqual(self._count(conn, "feature_decisions", "1.0.0"), 0)
            self.assertEqual(self._count(conn, "feature_opportunities", "1.0.0"), 0)

    def test_M02_single_hand_correspondence(self):
        with self.engine.connect() as conn:
            seeded = self._seed_main(conn)
        hand_id = seeded["hand_id"]
        a = seeded["action_ids"]
        result = self._materialize()
        self.assertEqual(
            (result.hands_processed, result.decision_rows, result.opportunity_rows),
            (1, 10, 8),
        )
        with self.engine.connect() as conn:
            decisions = self._decision_dump(conn, "1.0.0")
            opportunities = self._opp_dump(conn, "1.0.0")
        expected_decisions = [
            (hand_id, a[2], 2, "BTN", 0, "limped", 20000, "unopened", "limp"),
            (hand_id, a[3], 3, "SB", 0, "limped", 19500, "limped", "limp"),
            (hand_id, a[4], 4, "BB", 0, "limped", 19000, "limped", "check"),
            (hand_id, a[5], 3, "SB", 0, "limped", 19000, "unopened", "check"),
            (hand_id, a[6], 4, "BB", 0, "limped", 19000, "unopened", "small"),
            (hand_id, a[7], 2, "BTN", 0, "limped", 19000, "facing-raise", "check"),
            (hand_id, a[8], 3, "SB", 0, "limped", 19000, "facing-raise", "pot-sized-ish"),
            (hand_id, a[9], 4, "BB", 0, "limped", 18000, "facing-3bet", "call"),
            (hand_id, a[10], 3, "SB", 0, "limped", 16000, "unopened", "small"),
            (hand_id, a[11], 4, "BB", 0, "limped", 16000, "facing-raise", "check"),
        ]
        self.assertEqual(decisions, expected_decisions)
        expected_opportunities = [
            (hand_id, a[1], 0, 2, "CALL,FOLD,RAISE", ""),
            (hand_id, a[2], 0, 3, "CALL,FOLD,RAISE", ""),
            (hand_id, a[3], 0, 4, "CHECK,RAISE", ""),
            (hand_id, a[6], 1, 2, "CALL,FOLD,RAISE", "rainbow"),
            (hand_id, a[4], 1, 3, "BET,CHECK", "rainbow"),
            (hand_id, a[5], 1, 4, "BET,CHECK", "rainbow"),
            (hand_id, a[9], 2, 3, "BET,CHECK", "rainbow"),
            (hand_id, a[10], 2, 4, "CALL,FOLD,RAISE", "rainbow"),
        ]
        self.assertEqual(opportunities, expected_opportunities)
        # POSTs never produce decision rows; the terminal snapshot produces no
        # decision row and no opportunity row (actor is None).
        post_ids = {a[0], a[1]}
        self.assertEqual(post_ids & {row[1] for row in decisions}, set())
        self.assertEqual(len({(row[2], row[3]) for row in opportunities}), 8)
        # Dedupe: the re-opened (street 1, seat 3) snapshot keeps the FIRST
        # street-1 decision point (before the flop check), not the re-raise.
        self.assertEqual([row[1] for row in opportunities if row[2] == 1 and row[3] == 3], [a[4]])

    def test_M03_positions_vocabulary(self):
        with self.engine.connect() as conn:
            hu = self._seed_hu_call_check(conn, "HH-POS2")
            four = self._seed_fold_around(
                conn,
                "HH-POS4",
                ((2, "P4a", 200), (3, "P4b", 200), (4, "P4c", 200), (5, "P4d", 200)),
                (
                    (2, 5, "FOLD", None, None, None),
                    (3, 2, "FOLD", None, None, None),
                    (4, 3, "CALL", 10, "total_commitment", None),
                    (5, 4, "CHECK", None, None, None),
                ),
            )
            six = self._seed_fold_around(
                conn,
                "HH-POS6",
                tuple((seat, "P6%d" % seat, 200) for seat in range(2, 8)),
                tuple((seq, seat, "FOLD", None, None, None) for seq, seat in enumerate((5, 6, 7, 2), start=2))
                + ((6, 3, "CALL", 10, "total_commitment", None), (7, 4, "CHECK", None, None, None)),
            )
            nine = self._seed_fold_around(
                conn,
                "HH-POS9",
                tuple((seat, "P9%d" % seat, 200) for seat in range(2, 11)),
                tuple((seq, seat, "FOLD", None, None, None) for seq, seat in enumerate((5, 6, 7, 8, 9, 10, 2), start=2))
                + ((9, 3, "CALL", 10, "total_commitment", None), (10, 4, "CHECK", None, None, None)),
            )
        self._materialize()
        with self.engine.connect() as conn:
            opportunities = self._opp_dump(conn, "1.0.0")
            position_by_hand_seat = {
                (row[0], row[1]): row[2]
                for row in conn.execute(
                    _sql(
                        "SELECT hand_id, seat_number, position FROM"
                        " feature_decisions WHERE feature_version = '1.0.0'"
                    )
                ).fetchall()
            }
        by_hand = {}
        for row in opportunities:
            by_hand.setdefault(row[0], []).append(
                (row[3], position_by_hand_seat[(row[0], row[3])])
            )
        self.assertEqual(
            sorted(by_hand[hu["hand_id"]]), [(2, "BTN"), (3, "BB")]
        )
        self.assertEqual(
            sorted(by_hand[four["hand_id"]]),
            [(2, "BTN"), (3, "SB"), (4, "BB"), (5, "UTG")],
        )
        self.assertEqual(
            sorted(by_hand[six["hand_id"]]),
            [(2, "BTN"), (3, "SB"), (4, "BB"), (5, "UTG"), (6, "MP"), (7, "CO")],
        )
        # m = N - 3 interior seats for full ring: UTG, MP, MP, MP, MP, CO.
        self.assertEqual(
            sorted(by_hand[nine["hand_id"]]),
            [
                (2, "BTN"),
                (3, "SB"),
                (4, "BB"),
                (5, "UTG"),
                (6, "MP"),
                (7, "MP"),
                (8, "MP"),
                (9, "MP"),
                (10, "CO"),
            ],
        )

    def test_M04_initiative_cases(self):
        # Pure-builder level: the pinned rule fires on a synthetic sequence
        # where the deciding seat made the last aggression on the street.
        ActionKind = importlib.import_module("poker.domain.game").ActionKind
        build_initiative = self.schema.build_initiative
        self.assertEqual(
            build_initiative([(ActionKind.BET, 5), (ActionKind.CALL, 6)], 5), 1
        )
        self.assertEqual(
            build_initiative([(ActionKind.BET, 5), (ActionKind.RAISE, 6)], 5), 0
        )
        self.assertEqual(build_initiative([], 3), 0)
        # Integration level: engine-legal hands always carry 0 (the last
        # same-street aggressor never re-decides on that street — documented
        # structural finding, see schema.py).
        with self.engine.connect() as conn:
            init = self._seed_initiative(conn)
            main = self._seed_main(conn)
        self._materialize()
        with self.engine.connect() as conn:
            rows = {
                row[1]: row[4]
                for row in self._decision_dump(conn, "1.0.0")
                if row[0] == init["hand_id"]
            }
            main_rows = {
                row[1]: row[4]
                for row in self._decision_dump(conn, "1.0.0")
                if row[0] == main["hand_id"]
            }
        a = init["action_ids"]
        # seq8: Sb aggressed earlier this flop (its own BET at seq5) but the
        # LAST aggression is Btn's raise -> 0, not 1.
        self.assertEqual(rows[a[8]], 0)
        # seq7 (the re-opening raise): last aggression is Sb's bet -> 0.
        self.assertEqual(rows[a[7]], 0)
        # Prior street only: Sb's turn bet in F_MAIN follows flop aggression.
        self.assertEqual(main_rows[main["action_ids"][10]], 0)

    def test_M05_pot_types(self):
        with self.engine.connect() as conn:
            unopened = self._seed_unopened(conn)
            limped = self._seed_main(conn)
            single = self._seed_single_raised(conn)
            three = self._seed_three_bet(conn)
        self._materialize()
        with self.engine.connect() as conn:
            pot_types = {}
            for hand_id in (unopened["hand_id"], limped["hand_id"], single["hand_id"], three["hand_id"]):
                values = {
                    row[5]
                    for row in self._decision_dump(conn, "1.0.0")
                    if row[0] == hand_id
                }
                pot_types[hand_id] = values
        self.assertEqual(pot_types[unopened["hand_id"]], {"unopened"})
        self.assertEqual(pot_types[limped["hand_id"]], {"limped"})
        self.assertEqual(pot_types[single["hand_id"]], {"single-raised"})
        self.assertEqual(pot_types[three["hand_id"]], {"3bet+"})

    def test_M06_facing_actions(self):
        with self.engine.connect() as conn:
            single = self._seed_single_raised(conn)
            main = self._seed_main(conn)
            three = self._seed_three_bet(conn)
        self._materialize()
        with self.engine.connect() as conn:
            facing = {
                hand_id: [
                    row[7]
                    for row in self._decision_dump(conn, "1.0.0")
                    if row[0] == hand_id
                ]
                for hand_id in (single["hand_id"], main["hand_id"], three["hand_id"])
            }
        # single-raised: opener (0 raises 0 calls), two facing-raise callers,
        # then two unopened flop checks.
        self.assertEqual(
            facing[single["hand_id"]],
            ["unopened", "facing-raise", "facing-raise", "unopened", "unopened"],
        )
        # main: unopened opener, two limped callers, flop opener + bet-facer +
        # fold-facer + re-raise-facer + 3bet-facer, unopened turn opener,
        # facing-raise folder.
        self.assertEqual(
            facing[main["hand_id"]],
            [
                "unopened",
                "limped",
                "limped",
                "unopened",
                "unopened",
                "facing-raise",
                "facing-raise",
                "facing-3bet",
                "unopened",
                "facing-raise",
            ],
        )
        self.assertEqual(
            facing[three["hand_id"]],
            ["unopened", "facing-raise", "facing-3bet", "facing-4bet+", "facing-4bet+"],
        )

    def test_M07_sizing_buckets_precedence_and_edges(self):
        # Pure-builder level: BET into an empty pot fails closed.
        ActionKind = importlib.import_module("poker.domain.game").ActionKind
        Street = importlib.import_module("poker.domain.game").Street
        with self.assertRaises(ValueError) as ctx:
            self.schema.build_sizing_bucket(
                ActionKind.BET, 10, 0, 200, Street.FLOP, 0
            )
        self.assertIn("pot_before", str(ctx.exception))
        # Integration: heads-up posts (1/2) + TOTAL_TO call to the bb add only
        # 1 more chip (in_front 1), so the flop pot is 4; bets of 2/3/4/5 give
        # permille ratios 500/750/1000/1250 across the bucket boundaries; the
        # exact 600 boundary comes from F_INIT's flop raise (30 into 50).
        with self.engine.connect() as conn:
            small = self._seed_sizing(conn, "HH-SIZE-SMALL", 2)  # 500 -> small
            psi_low = self._seed_sizing(conn, "HH-SIZE-PSI-LOW", 3)  # 750
            psi_high = self._seed_sizing(conn, "HH-SIZE-PSI-HIGH", 4)  # 1000
            overbet = self._seed_sizing(conn, "HH-SIZE-OVER", 5)  # 1250
            boundary = self._seed_initiative(conn)  # seq7 raise: 600 -> small
            allin = self._seed_all_in(conn)
            main = self._seed_main(conn)
        self._materialize()
        with self.engine.connect() as conn:
            buckets = {
                hand_id: [
                    row[8]
                    for row in self._decision_dump(conn, "1.0.0")
                    if row[0] == hand_id
                ]
                for hand_id in (
                    small["hand_id"],
                    psi_low["hand_id"],
                    psi_high["hand_id"],
                    overbet["hand_id"],
                    boundary["hand_id"],
                    allin["hand_id"],
                    main["hand_id"],
                )
            }
            boundary_buckets = {
                row[1]: row[8]
                for row in self._decision_dump(conn, "1.0.0")
                if row[0] == boundary["hand_id"]
            }
        base = ["limp", "check"]
        self.assertEqual(buckets[small["hand_id"]], base + ["small", "check"])
        self.assertEqual(buckets[psi_low["hand_id"]], base + ["pot-sized-ish", "check"])
        self.assertEqual(buckets[psi_high["hand_id"]], base + ["pot-sized-ish", "check"])
        self.assertEqual(buckets[overbet["hand_id"]], base + ["overbet", "check"])
        # 600 is inclusive 'small' (raise 30 into pot 50).
        self.assertEqual(boundary_buckets[boundary["action_ids"][7]], "small")
        # all-in: the shove equals the remaining stack (precedence before ratio).
        self.assertEqual(buckets[allin["hand_id"]], base + ["all-in"])
        # Precedence: CALL resolves to 'call' before the ratio/all-in paths
        # (F_MAIN seq9: Bb calls 20 into a 70-chip pot — the ratio would be
        # 'pot-sized-ish', but the pinned CALL precedence wins).
        self.assertEqual(buckets[main["hand_id"]][7], "call")

    def test_M08_board_descriptors(self):
        with self.engine.connect() as conn:
            monotone = self._seed_check_line(
                conn,
                "HH-BRD-MONO",
                ((1, "2h", 1), (1, "7h", 2), (1, "9h", 3), (2, "2s", 1), (3, "Ks", 1)),
            )
            two_tone = self._seed_check_line(
                conn,
                "HH-BRD-TT",
                ((1, "2h", 1), (1, "7h", 2), (1, "9d", 3), (2, "9s", 1)),
            )
            main = self._seed_main(conn)
        self._materialize()
        with self.engine.connect() as conn:
            descriptors = {}
            for hand_id in (monotone["hand_id"], two_tone["hand_id"], main["hand_id"]):
                per_street = {}
                for row in self._opp_dump(conn, "1.0.0"):
                    if row[0] == hand_id:
                        per_street[row[2]] = row[5]
                descriptors[hand_id] = per_street
        # monotone flop; turn adds 2s (pair + second suit) -> two-tone-paired;
        # river is cumulative: the 2s rank dup persists -> two-tone-paired.
        self.assertEqual(
            descriptors[monotone["hand_id"]],
            {0: "", 1: "monotone", 2: "two-tone-paired", 3: "two-tone-paired"},
        )
        self.assertEqual(
            descriptors[two_tone["hand_id"]],
            {0: "", 1: "two-tone", 2: "rainbow-paired", 3: "rainbow-paired"},
        )
        self.assertEqual(descriptors[main["hand_id"]][0], "")
        self.assertEqual(descriptors[main["hand_id"]][1], "rainbow")
        # 'monotone-paired' is unreachable: a duplicated rank requires a second
        # suit and identical cards are forbidden (documented in schema.py).

    def test_M09_effective_stack_milli_bb(self):
        # Pure-builder level: exact INTEGER permille math, planner formula.
        self.assertEqual(
            self.schema.build_effective_stack_milli_bb(20000, 200), 100000
        )
        # 1001 chips / bb 100 -> 10010 (planner formula chips*1000//bb;
        # M09's illustrative '10000 floor' corresponds to (chips//bb)*1000 —
        # documented deviation, planner wording is authoritative).
        self.assertEqual(self.schema.build_effective_stack_milli_bb(1001, 100), 10010)
        with self.assertRaises(ValueError) as ctx:
            self.schema.build_effective_stack_milli_bb(100, 0)
        self.assertIn("big_blind", str(ctx.exception))
        with self.engine.connect() as conn:
            seeded = self._seed_main(conn)
        self._materialize()
        with self.engine.connect() as conn:
            values = [
                (row[1], row[6], self._scalar(
                    conn,
                    "SELECT typeof(effective_stack_milli_bb) FROM feature_decisions"
                    " WHERE hand_id = :h AND action_id = :a",
                    {"h": seeded["hand_id"], "a": row[1]},
                ))
                for row in self._decision_dump(conn, "1.0.0")
            ]
        self.assertEqual(
            values,
            [
                (seeded["action_ids"][2], 20000, "integer"),
                (seeded["action_ids"][3], 19500, "integer"),
                (seeded["action_ids"][4], 19000, "integer"),
                (seeded["action_ids"][5], 19000, "integer"),
                (seeded["action_ids"][6], 19000, "integer"),
                (seeded["action_ids"][7], 19000, "integer"),
                (seeded["action_ids"][8], 19000, "integer"),
                (seeded["action_ids"][9], 18000, "integer"),
                (seeded["action_ids"][10], 16000, "integer"),
                (seeded["action_ids"][11], 16000, "integer"),
            ],
        )

    def test_M10_name_seat_mapping(self):
        with self.engine.connect() as conn:
            dup_hand = self._seed_duplicate_names(conn)
            seeded = self._seed_main(conn)
        with self.assertRaises(ValueError) as ctx:
            self._materialize()
        self.assertIn("Dup", str(ctx.exception))
        self.assertIn("duplicate", str(ctx.exception).lower())
        # The adapter is per-hand: the valid hand materializes on its own.
        result = self._materialize(hand_ids=[seeded["hand_id"]])
        self.assertEqual(result.hands_processed, 1)
        with self.engine.connect() as conn:
            self.assertEqual(
                self._count(conn, "feature_decisions", "1.0.0", dup_hand), 0,
                "a duplicate-name hand must produce no feature rows",
            )
            rows = [
                row for row in self._decision_dump(conn, "1.0.0")
                if row[0] == seeded["hand_id"]
            ]
        # Positive path: snapshot actor names resolve to seats deterministically.
        self.assertEqual(rows[0][2], 2)
        self.assertEqual(rows[0][3], "BTN")

    def test_M11_zero_voluntary_hand(self):
        with self.engine.connect() as conn:
            seeded = self._seed_all_in_posts(conn)
        result = self._materialize()
        # Engine semantics: a posts-only hand (both blinds all-in) sets no
        # snapshots at all — the terminal flag is only raised by voluntary
        # actions — so zero rows of both kinds is the engine-derived outcome.
        self.assertEqual(
            (result.hands_processed, result.decision_rows, result.opportunity_rows),
            (1, 0, 0),
        )
        with self.engine.connect() as conn:
            self.assertEqual(self._count(conn, "feature_decisions", "1.0.0", seeded["hand_id"]), 0)
            self.assertEqual(self._count(conn, "feature_opportunities", "1.0.0", seeded["hand_id"]), 0)

    def test_M12_cards_optional(self):
        with self.engine.connect() as conn:
            no_board = self._seed_single_raised(conn)  # flop actions, NO board rows
            main = self._seed_main(conn)
        self._materialize()
        with self.engine.connect() as conn:
            self.assertEqual(self._scalar(conn, "SELECT COUNT(*) FROM hole_cards"), 0)
            descriptors = {
                (row[2], row[3]): row[5]
                for row in self._opp_dump(conn, "1.0.0")
                if row[0] == no_board["hand_id"]
            }
        # Absent board rows are treated as empty, not an error.
        self.assertEqual(descriptors[(1, 3)], "")
        self.assertEqual(descriptors[(1, 4)], "")
        self.assertGreater(len(self._decision_rows_of(main["hand_id"])), 0)

    def _decision_rows_of(self, hand_id):
        with self.engine.connect() as conn:
            return [
                row for row in self._decision_dump(conn, "1.0.0") if row[0] == hand_id
            ]


# ---------------------------------------------------------------------------
# R01-R05: recompute semantics
# ---------------------------------------------------------------------------


class RecomputeContractTests(FeaturesContractBase):
    def test_R01_raw_tables_untouched(self):
        with self.engine.connect() as conn:
            self._seed_main(conn)
            self._seed_hu_call_check(conn, "HH-R01B", names=("R1a", "R1b"))
            before = self._raw_digest(conn)
        result = self._materialize()
        self.assertEqual(result.hands_processed, 2)
        with self.engine.connect() as conn:
            after = self._raw_digest(conn)
            decisions = self._count(conn, "feature_decisions", "1.0.0")
            opportunities = self._count(conn, "feature_opportunities", "1.0.0")
        self.assertEqual(before, after)
        self.assertGreater(decisions, 0)
        self.assertGreater(opportunities, 0)

    def test_R02_recompute_idempotent(self):
        with self.engine.connect() as conn:
            seeded = self._seed_main(conn)
        self._materialize()
        with self.engine.connect() as conn:
            first_decisions = self._decision_dump(conn, "1.0.0")
            first_opps = self._opp_dump(conn, "1.0.0")
        result = self._materialize()
        self.assertEqual(result.decision_rows, len(first_decisions))
        self.assertEqual(result.opportunity_rows, len(first_opps))
        with self.engine.connect() as conn:
            second_decisions = self._decision_dump(conn, "1.0.0")
            second_opps = self._opp_dump(conn, "1.0.0")
        self.assertEqual(first_decisions, second_decisions)
        self.assertEqual(first_opps, second_opps)

    def test_R03_version_bump_coexistence(self):
        with self.engine.connect() as conn:
            seeded = self._seed_main(conn)
        self._materialize()
        with self.engine.connect() as conn:
            v1_decisions = self._decision_dump(conn, "1.0.0")
            v1_opps = self._opp_dump(conn, "1.0.0")
        self._materialize(version="2.0.0")
        with self.engine.connect() as conn:
            self.assertEqual(self._decision_dump(conn, "1.0.0"), v1_decisions)
            self.assertEqual(self._opp_dump(conn, "1.0.0"), v1_opps)
            v2_decisions = self._decision_dump(conn, "2.0.0")
            v2_opps = self._opp_dump(conn, "2.0.0")
        # Same feature values under the new version (version column excluded).
        self.assertEqual(
            [row[1:] for row in v2_decisions], [row[1:] for row in v1_decisions]
        )
        self.assertEqual([row[1:] for row in v2_opps], [row[1:] for row in v1_opps])
        # Delete-by-version removes only its own rows on recompute.
        self._materialize(version="2.0.0")
        with self.engine.connect() as conn:
            self.assertEqual(self._decision_dump(conn, "1.0.0"), v1_decisions)
            self.assertEqual(self._opp_dump(conn, "1.0.0"), v1_opps)
            self.assertEqual(len(self._decision_dump(conn, "2.0.0")), len(v2_decisions))

    def test_R04_chunked_partial_recompute(self):
        with self.engine.connect() as conn:
            first = self._seed_main(conn, "HH-R04A")
            second = self._seed_hu_call_check(conn, "HH-R04B", names=("R4a", "R4b"))
        self._materialize()
        with self.engine.connect() as conn:
            full_decisions = self._decision_dump(conn, "1.0.0")
            full_opps = self._opp_dump(conn, "1.0.0")
        result = self._materialize(hand_ids=[first["hand_id"]])
        self.assertEqual(result.hands_processed, 1)
        with self.engine.connect() as conn:
            re_decisions = self._decision_dump(conn, "1.0.0")
            re_opps = self._opp_dump(conn, "1.0.0")
        self.assertEqual(re_decisions, full_decisions)
        self.assertEqual(re_opps, full_opps)
        # The untouched hand's rows are byte-identical (created_at excluded).
        other = [row for row in full_decisions if row[0] == second["hand_id"]]
        self.assertEqual(
            [row for row in re_decisions if row[0] == second["hand_id"]], other
        )

    def test_R05_partial_failure_recovery(self):
        with self.engine.connect() as conn:
            good = self._seed_hu_call_check(conn, "HH-R05A")
            bad = self._seed_wrong_turn(conn, "HH-R05B")
        with self.assertRaises(self.state_module.WrongTurnError):
            self._materialize()
        with self.engine.connect() as conn:
            good_decisions = [
                row for row in self._decision_dump(conn, "1.0.0")
                if row[0] == good["hand_id"]
            ]
            self.assertEqual(len(good_decisions), 2)
            self.assertEqual(
                self._count(conn, "feature_decisions", "1.0.0", bad["hand_id"]), 0
            )
            self.assertEqual(
                self._count(conn, "feature_opportunities", "1.0.0", bad["hand_id"]), 0
            )


# ---------------------------------------------------------------------------
# D01-D02: determinism
# ---------------------------------------------------------------------------


class DeterminismContractTests(FeaturesContractBase):
    def _build_two_hand_db(self, engine):
        from sqlalchemy import text as sa_text

        with engine.connect() as conn:
            conn.execute(sa_text(
                "INSERT INTO raw_sources(name) VALUES ('src-D01')"
            ))
            self._seed_main(conn, "HH-D01A")
            self._seed_hu_call_check(conn, "HH-D01B", names=("D1a", "D1b"))
        return engine

    def test_D01_determinism_byte_identical(self):
        engine_a = self._build_two_hand_db(self.engine)
        engine_b = self._build_two_hand_db(self._extra_engine())
        result_a = self.materialize.materialize_features(engine_a)
        result_b = self.materialize.materialize_features(engine_b)
        self.assertEqual(
            (result_a.hands_processed, result_a.decision_rows, result_a.opportunity_rows),
            (result_b.hands_processed, result_b.decision_rows, result_b.opportunity_rows),
        )
        with engine_a.connect() as conn_a, engine_b.connect() as conn_b:
            self.assertEqual(
                self._decision_dump(conn_a, "1.0.0"),
                self._decision_dump(conn_b, "1.0.0"),
            )
            self.assertEqual(
                self._opp_dump(conn_a, "1.0.0"), self._opp_dump(conn_b, "1.0.0")
            )

    def test_D02_chunk_composition_equals_full(self):
        engine_chunk = self._extra_engine()
        from sqlalchemy import text as sa_text

        with engine_chunk.connect() as conn:
            conn.execute(sa_text("INSERT INTO raw_sources(name) VALUES ('src-D02')"))
            hands = [
                self._seed_hu_call_check(conn, "HH-D02A", names=("D2a", "D2b"))["hand_id"],
                self._seed_hu_call_check(conn, "HH-D02B", names=("D2c", "D2d"))["hand_id"],
                self._seed_hu_call_check(conn, "HH-D02C", names=("D2e", "D2f"))["hand_id"],
            ]
        self.materialize.materialize_features(engine_chunk, hand_ids=hands[:2])
        self.materialize.materialize_features(engine_chunk, hand_ids=[hands[2]])
        engine_full = self._extra_engine()
        with engine_full.connect() as conn:
            conn.execute(sa_text("INSERT INTO raw_sources(name) VALUES ('src-D02F')"))
            self._seed_hu_call_check(conn, "HH-D02A", names=("D2a", "D2b"))
            self._seed_hu_call_check(conn, "HH-D02B", names=("D2c", "D2d"))
            self._seed_hu_call_check(conn, "HH-D02C", names=("D2e", "D2f"))
        self.materialize.materialize_features(engine_full)
        with engine_chunk.connect() as conn_c, engine_full.connect() as conn_f:
            self.assertEqual(
                self._decision_dump(conn_c, "1.0.0"), self._decision_dump(conn_f, "1.0.0")
            )
            self.assertEqual(
                self._opp_dump(conn_c, "1.0.0"), self._opp_dump(conn_f, "1.0.0")
            )


# ---------------------------------------------------------------------------
# F01-F02: fail-closed
# ---------------------------------------------------------------------------


class FailClosedContractTests(FeaturesContractBase):
    def test_F01_bb_zero_fail_closed(self):
        with self.engine.connect() as conn:
            good = self._seed_hu_call_check(conn, "HH-F01A", names=("F1a", "F1b"))
            bad = self._seed_bb_zero(conn, "HH-F01B", with_bet=False)
        with self.assertRaises(ValueError) as ctx:
            self._materialize()
        self.assertIn(str(bad["hand_id"]), str(ctx.exception))
        self.assertIn("big_blind", str(ctx.exception))
        with self.engine.connect() as conn:
            self.assertGreater(
                self._count(conn, "feature_decisions", "1.0.0", good["hand_id"]), 0,
                "the earlier committed hand keeps its feature rows",
            )
            self.assertEqual(
                self._count(conn, "feature_decisions", "1.0.0", bad["hand_id"]), 0
            )
            self.assertEqual(
                self._count(conn, "feature_opportunities", "1.0.0", bad["hand_id"]), 0
            )

    def test_F02_replay_illegal_propagates(self):
        with self.engine.connect() as conn:
            bad = self._seed_wrong_turn(conn, "HH-F02")
        with self.assertRaises(self.state_module.WrongTurnError) as ctx:
            self._materialize()
        self.assertIn("WtSb", str(ctx.exception))
        with self.engine.connect() as conn:
            self.assertEqual(
                self._count(conn, "feature_decisions", "1.0.0", bad["hand_id"]), 0
            )
            self.assertEqual(
                self._count(conn, "feature_opportunities", "1.0.0", bad["hand_id"]), 0
            )


# ---------------------------------------------------------------------------
# T01-T03: RED-discipline structure
# ---------------------------------------------------------------------------


class RedDisciplineTests(FeaturesContractBase):
    """Structural pins for the two-phase lazy-import RED design (T01-T03).

    Inherits the lazy-import setUp so that in Phase A every one of the 33
    tests reports ModuleNotFoundError 'No module named poker.features'
    individually (designer T01), while the structural pins still hold on GREEN.
    """

    @staticmethod
    def _source():
        with open(os.path.abspath(__file__), encoding="utf-8") as handle:
            return handle.read()

    def test_T01_no_module_level_poker_imports(self):
        # Phase A depends on EVERY test failing per-test at the lazy import
        # inside setUp; a module-level poker import would break collection.
        for lineno, line in enumerate(self._source().splitlines(), 1):
            if re.match(r"^(import|from)\s+poker\b", line):
                self.fail("module-level poker import at line %d: %s" % (lineno, line))

    def test_T02_package_marker_present(self):
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
        self.assertTrue(os.path.isfile(marker), "tests/features/__init__.py must exist")

    def test_T03_lazy_imports_pin_phase_discipline(self):
        source = self._source()
        self.assertIn('importlib.import_module("poker.features")', source)
        self.assertIn('importlib.import_module("poker.db.models")', source)
        self.assertIn('importlib.import_module("poker.db.session")', source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
