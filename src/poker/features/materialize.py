"""Materialize versioned derived feature rows from the normalized store.

Task POKER-DATA-FEATURES-001 (planner steps 8-11 pin; risk R2, TDD). Public
API (frozen by tests/features/test_materialize.py):

* ``MaterializeResult`` — frozen dataclass ``(hands_processed, decision_rows,
  opportunity_rows)``.
* ``materialize_features(engine, hand_ids=None, feature_version=FEATURES_VERSION)``.

Pinned semantics:

* SANCTIONED legal-action authority: ``StateEngine.replay`` — features never
  hand-roll legality; replay failures (``InconsistentHistoryError``,
  ``WrongTurnError``, ...) propagate and the failing hand leaves NO partial
  feature rows because each hand is processed inside its own transaction
  (previously committed hands stay valid).
* Correspondence (AC1): the engine emits one DecisionSnapshot BEFORE each
  voluntary action; a ``feature_decisions`` row is built per voluntary
  non-POST action ``actions[j]`` from the snapshot whose provenance prefix
  ends at ``action_index == j - 1`` (enforced by an explicit fail-closed
  check, never assumed); the terminal snapshot produces only an opportunity
  row when it carries an actor (it never does); POSTs never produce decision
  rows.
* Opportunities: one row per ``(hand_id, street, seat_number,
  feature_version)`` pre-action snapshot with ``actor_to_act != None``.
  Multiway re-opens can present the SAME ``(street, seat)`` twice; the FIRST
  snapshot wins (documented first-wins dedupe; the UNIQUE constraint pins the
  identity). ``action_id`` is the last applied action BEFORE the decision
  point (``actions[snapshot.action_index]``); NULL only for a hand's very
  first decision (``action_index == -1``).
* Snapshot keys are player NAMES; the DB->domain adapter maps name<->seat
  from ``hand_players JOIN players`` (positional, seat-ordered) and fails
  closed on duplicate player names within one hand.
* Recompute (AC2): per-hand transaction deletes BOTH feature tables WHERE
  ``feature_version = :v AND hand_id = :h`` then re-inserts; one hand per
  transaction; the raw normalized tables are NEVER written by this module.
* Determinism (AC3): reads are ORDER BY hand_id / seat_number / sequence /
  street+position; feature values contain no wall-clock, randomness or
  dict-iteration order (``created_at`` is server-side only and excluded from
  all value comparisons).
* Fail-closed conversions: ``big_blind == 0`` raises ``ValueError`` naming
  the hand (permille conversion); unknown action kinds, unknown amount
  semantics and duplicate names raise ``ValueError``.

DB semantics mapping (documented, not silent; no importer.py exists on main
so this adapter owns the canonical DB->domain translation): POST rows carry
NULL semantics in the DB and are replayed with ``AmountSemantics.ADDED`` (a
blind/ante post adds its amount, clamped by the engine); ``'total_commitment'``
maps to ``TOTAL_TO``; ``'chips_added'`` maps to ``ADDED``; the enum value
names ``'total_to'``/``'added'`` are accepted aliases; anything else fails
closed. Street-scoped ``in_front`` for sizing deltas is re-derived with the
production function ``poker.domain.pots.normalize_contributions`` on the
street-filtered provenance prefix (the same path the engine's own test
oracle uses), never hand-rolled accounting.
"""

from __future__ import annotations

import dataclasses

from sqlalchemy import text

from poker.domain.game import (
    Action,
    ActionKind,
    AmountSemantics,
    BlindAnteConfig,
    Player,
    Seat,
    Street,
    TableMetadata,
)
from poker.domain.money import Chips
from poker.domain.pots import normalize_contributions
from poker.engine import rules
from poker.engine.state import StateEngine

from . import schema as features
from .schema import FEATURES_VERSION

__all__ = ["FEATURES_VERSION", "MaterializeResult", "materialize_features"]

_DECISION_INSERT = text(
    "INSERT INTO feature_decisions (hand_id, action_id, feature_version,"
    " seat_number, position, initiative, pot_type, effective_stack_milli_bb,"
    " facing_action, sizing_bucket) VALUES (:hand_id, :action_id,"
    " :feature_version, :seat_number, :position, :initiative, :pot_type,"
    " :effective_stack_milli_bb, :facing_action, :sizing_bucket)"
)
_OPPORTUNITY_INSERT = text(
    "INSERT INTO feature_opportunities (hand_id, action_id, feature_version,"
    " street, seat_number, legal_actions, board_descriptor) VALUES (:hand_id,"
    " :action_id, :feature_version, :street, :seat_number, :legal_actions,"
    " :board_descriptor)"
)

_SEMANTICS_ALIASES = {
    "total_commitment": AmountSemantics.TOTAL_TO,
    "total_to": AmountSemantics.TOTAL_TO,
    "chips_added": AmountSemantics.ADDED,
    "added": AmountSemantics.ADDED,
}


@dataclasses.dataclass(frozen=True)
class MaterializeResult:
    """Counts returned by :func:`materialize_features` (O4 oracle)."""

    hands_processed: int
    decision_rows: int
    opportunity_rows: int


def materialize_features(engine, hand_ids=None, feature_version=FEATURES_VERSION):
    """Materialize/recompute feature rows for the selected hands.

    ``hand_ids=None`` processes every hand ordered by ``hands.id``; a
    sequence processes exactly the known ids from that sequence (unknown ids
    fail closed; duplicates collapse). Recompute deletes only rows of
    ``feature_version`` for the processed hands before re-inserting, so
    versions coexist and other hands are untouched.
    """
    if not isinstance(feature_version, str) or not feature_version:
        raise ValueError("feature_version must be a non-empty string")
    with engine.connect() as conn:
        ordered = [
            int(row[0])
            for row in conn.execute(text("SELECT id FROM hands ORDER BY id"))
        ]
    if hand_ids is None:
        targets = ordered
    else:
        wanted = {int(hand_id) for hand_id in hand_ids}
        unknown = sorted(wanted - set(ordered))
        if unknown:
            raise ValueError(
                "unknown hand_ids for materialize_features: %s" % unknown
            )
        targets = [hand_id for hand_id in ordered if hand_id in wanted]
    decision_rows = 0
    opportunity_rows = 0
    for hand_id in targets:
        decisions, opportunities = _materialize_hand(engine, hand_id, feature_version)
        decision_rows += decisions
        opportunity_rows += opportunities
    return MaterializeResult(len(targets), decision_rows, opportunity_rows)


# ---------------------------------------------------------------------------
# per-hand pipeline (one transaction per hand)
# ---------------------------------------------------------------------------


def _materialize_hand(engine, hand_id, feature_version):
    """Read + replay + rebuild one hand inside a single transaction.

    Any error (replay failure, conversion failure) aborts the transaction so
    the hand keeps no partial feature rows.
    """
    with engine.begin() as txn:
        hand_row = txn.execute(
            text(
                "SELECT small_blind, big_blind, ante, button_seat FROM hands"
                " WHERE id = :h"
            ),
            {"h": hand_id},
        ).fetchone()
        if hand_row is None:
            raise ValueError("hand %d disappeared during materialization" % hand_id)
        small_blind, big_blind, ante, button_seat = (int(v) for v in hand_row)
        if big_blind <= 0:
            # Fail closed naming the hand (hands CHECK only enforces >= 0);
            # the permille conversion below can never divide by zero.
            raise ValueError(
                "hand %d: big_blind must be positive to convert "
                "effective_stack_milli_bb (INTEGER permille-of-bb); got %d"
                % (hand_id, big_blind)
            )
        seat_rows = txn.execute(
            text(
                "SELECT hp.seat_number, p.name, hp.stack FROM hand_players hp"
                " JOIN players p ON p.id = hp.player_id WHERE hp.hand_id = :h"
                " ORDER BY hp.seat_number"
            ),
            {"h": hand_id},
        ).fetchall()
        if len(seat_rows) < 2:
            raise ValueError(
                "hand %d needs at least 2 seated players for replay (got %d)"
                % (hand_id, len(seat_rows))
            )
        names = [str(row[1]) for row in seat_rows]
        if len(set(names)) != len(names):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            raise ValueError(
                "hand %d has duplicate player names; snapshot keys are player "
                "names and must be unique within one hand: %s"
                % (hand_id, duplicates)
            )
        action_rows = txn.execute(
            text(
                "SELECT id, seat_number, sequence, street, action_type, amount,"
                " semantics, post_type FROM actions WHERE hand_id = :h"
                " ORDER BY sequence"
            ),
            {"h": hand_id},
        ).fetchall()
        board_rows = txn.execute(
            text(
                "SELECT street, card FROM board_cards WHERE hand_id = :h"
                " ORDER BY street, position"
            ),
            {"h": hand_id},
        ).fetchall()

        name_by_seat = {int(seat): str(name) for seat, name, _stack in seat_rows}
        seat_of = {name: seat for seat, name in name_by_seat.items()}
        actions = [
            _action_from_row(hand_id, row, name_by_seat) for row in action_rows
        ]
        action_ids = [int(row[0]) for row in action_rows]
        table, blinds = _build_table(seat_rows, small_blind, big_blind, ante)
        # Replay errors fail the hand loudly: features never fabricate
        # legality (planner step 10).
        snapshots, _state_result = StateEngine.replay(
            table, blinds, actions, button_seat=button_seat
        )

        board_pairs = [(int(row[0]), str(row[1])) for row in board_rows]
        positions = features.build_positions(
            sorted(name_by_seat), button_seat
        )
        pot_type = features.build_pot_type(
            [action.kind for action in actions if action.street is Street.PREFLOP]
        )

        decision_values = _build_decision_values(
            hand_id,
            feature_version,
            actions,
            action_ids,
            snapshots,
            seat_of,
            positions,
            pot_type,
            big_blind,
        )
        opportunity_values = _build_opportunity_values(
            hand_id,
            feature_version,
            actions,
            action_ids,
            snapshots,
            seat_of,
            board_pairs,
        )

        txn.execute(
            text(
                "DELETE FROM feature_decisions WHERE feature_version = :v"
                " AND hand_id = :h"
            ),
            {"v": feature_version, "h": hand_id},
        )
        txn.execute(
            text(
                "DELETE FROM feature_opportunities WHERE feature_version = :v"
                " AND hand_id = :h"
            ),
            {"v": feature_version, "h": hand_id},
        )
        if decision_values:
            txn.execute(
                _DECISION_INSERT,
                [dict(zip(_DECISION_COLUMNS, row)) for row in decision_values],
            )
        if opportunity_values:
            txn.execute(
                _OPPORTUNITY_INSERT,
                [dict(zip(_OPPORTUNITY_COLUMNS, row)) for row in opportunity_values],
            )
    return len(decision_values), len(opportunity_values)


_DECISION_COLUMNS = (
    "hand_id",
    "action_id",
    "feature_version",
    "seat_number",
    "position",
    "initiative",
    "pot_type",
    "effective_stack_milli_bb",
    "facing_action",
    "sizing_bucket",
)
_OPPORTUNITY_COLUMNS = (
    "hand_id",
    "action_id",
    "feature_version",
    "street",
    "seat_number",
    "legal_actions",
    "board_descriptor",
)


def _build_table(seat_rows, small_blind, big_blind, ante):
    """TableMetadata pairs players/seats positionally, seat-ordered."""
    players = tuple(
        Player(str(name), Chips(int(stack))) for _seat, name, stack in seat_rows
    )
    seats = tuple(Seat(int(seat)) for seat, _name, _stack in seat_rows)
    blinds = BlindAnteConfig(Chips(small_blind), Chips(big_blind), Chips(ante))
    return TableMetadata(players, seats, blinds), blinds


def _action_from_row(hand_id, row, name_by_seat):
    """DB actions row -> canonical Action (fail closed on unknown values)."""
    action_id, seat_number, sequence, street, action_type, amount, semantics, _post_type = row
    actor = name_by_seat.get(int(seat_number))
    if actor is None:
        raise ValueError(
            "hand %d action %d attributes seat %s with no seated player"
            % (hand_id, int(action_id), seat_number)
        )
    try:
        kind = ActionKind[str(action_type)]
    except KeyError:
        raise ValueError(
            "hand %d action %d has unknown action_type %r"
            % (hand_id, int(action_id), str(action_type))
        ) from None
    chips = None if amount is None else Chips(int(amount))
    if kind is ActionKind.POST:
        # POST rows carry NULL semantics in the DB (action-shape CHECK); a
        # blind/ante post adds its amount (engine U3 clamps short posts).
        amount_semantics = AmountSemantics.ADDED
    elif kind in (ActionKind.CALL, ActionKind.BET, ActionKind.RAISE):
        if chips is None:
            raise ValueError(
                "hand %d action %d (%s) must carry a chip amount"
                % (hand_id, int(action_id), kind.name)
            )
        amount_semantics = _semantics_for(hand_id, action_id, semantics)
    else:  # FOLD/CHECK
        amount_semantics = None
    return Action(
        actor,
        Street(int(street)),
        kind,
        chips,
        amount_semantics,
        int(sequence),
        "db:actions:%d" % int(action_id),
    )


def _semantics_for(hand_id, action_id, semantics):
    if semantics is None:
        raise ValueError(
            "hand %d action %d carries a chip amount without semantics"
            % (hand_id, int(action_id))
        )
    mapped = _SEMANTICS_ALIASES.get(str(semantics))
    if mapped is None:
        raise ValueError(
            "hand %d action %d has unknown amount semantics %r (accepted: %s)"
            % (hand_id, int(action_id), str(semantics), sorted(_SEMANTICS_ALIASES))
        )
    return mapped


def _build_decision_values(
    hand_id,
    feature_version,
    actions,
    action_ids,
    snapshots,
    seat_of,
    positions,
    pot_type,
    big_blind,
):
    """One feature_decisions row per voluntary non-POST action (AC1)."""
    voluntary = [action for action in actions if action.kind is not ActionKind.POST]
    acting_snapshots = [
        snapshot for snapshot in snapshots if snapshot.actor_to_act is not None
    ]
    if len(voluntary) != len(acting_snapshots):
        raise ValueError(
            "hand %d: engine snapshot/action correspondence broken (%d "
            "voluntary actions vs %d acting snapshots)"
            % (hand_id, len(voluntary), len(acting_snapshots))
        )
    index_of = {id(action): index for index, action in enumerate(actions)}
    rows = []
    for action, snapshot in zip(voluntary, acting_snapshots):
        action_index = index_of[id(action)]
        # Snapshot provenance prefix ends at action_index - 1 (U1 pin,
        # enforced not assumed).
        if snapshot.action_index != action_index - 1:
            raise ValueError(
                "hand %d: snapshot correspondence mismatch (action at index %d, "
                "snapshot.action_index %d)"
                % (hand_id, action_index, snapshot.action_index)
            )
        actor = action.actor
        actor_seat = seat_of[actor]
        street = action.street
        prefix = actions[:action_index]
        same_street = [step for step in prefix if step.street is street]
        raise_count = sum(
            1
            for step in same_street
            if step.kind in (ActionKind.BET, ActionKind.RAISE)
        )
        call_count = sum(1 for step in same_street if step.kind is ActionKind.CALL)
        facing = features.build_facing_action(raise_count, call_count)
        initiative = features.build_initiative(
            [(step.kind, seat_of[step.actor]) for step in same_street],
            actor_seat,
        )
        sizing = _sizing_bucket(hand_id, action, snapshot, same_street, actor, raise_count)
        milli_bb = features.build_effective_stack_milli_bb(
            int(snapshot.effective_stack[actor]), big_blind
        )
        rows.append(
            (
                hand_id,
                action_ids[action_index],
                feature_version,
                actor_seat,
                positions[actor_seat],
                initiative,
                pot_type,
                milli_bb,
                facing,
                sizing,
            )
        )
    return rows


def _sizing_bucket(hand_id, action, snapshot, same_street, actor, raise_count):
    if action.amount is None:  # FOLD/CHECK: amountless bucket
        return features.build_sizing_bucket(
            action.kind, 0, snapshot.pot, 0, action.street, raise_count
        )
    in_front = normalize_contributions(same_street).get(actor, Chips(0)).value
    delta = rules.amount_delta(
        int(action.amount.value), action.amount_semantics, in_front
    )
    if delta is None:
        raise ValueError(
            "hand %d: impossible TOTAL_TO history for action %s (sizing delta)"
            % (hand_id, action.provenance)
        )
    return features.build_sizing_bucket(
        action.kind,
        delta,
        int(snapshot.pot),
        int(snapshot.effective_stack[actor]),
        action.street,
        raise_count,
    )


def _build_opportunity_values(
    hand_id,
    feature_version,
    actions,
    action_ids,
    snapshots,
    seat_of,
    board_pairs,
):
    """One opportunity row per (street, seat) pre-action snapshot; first wins."""
    seen = set()
    rows = []
    for snapshot in snapshots:
        actor = snapshot.actor_to_act
        if actor is None:
            continue
        actor_seat = seat_of[actor]
        street_value = int(snapshot.street)
        key = (street_value, actor_seat)
        if key in seen:
            continue  # first-wins dedupe (multiway re-opens)
        seen.add(key)
        legal_actions = features.build_legal_actions_string(snapshot.legal_actions)
        descriptor = features.build_board_descriptor(board_pairs, street_value)
        pre_action_id = (
            action_ids[snapshot.action_index] if snapshot.action_index >= 0 else None
        )
        rows.append(
            (
                hand_id,
                pre_action_id,
                feature_version,
                street_value,
                actor_seat,
                legal_actions,
                descriptor,
            )
        )
    return rows
