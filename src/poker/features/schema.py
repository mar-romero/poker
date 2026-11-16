"""Pure feature vocabulary and builders for derived poker features.

Task POKER-DATA-FEATURES-001 (planner steps 8/11 pin; risk R2, TDD). This
module owns the closed vocabularies and the PURE feature builders; all IO
lives in :mod:`poker.features.materialize` (DB reads, replay orchestration,
row writes). Builders are pure functions of their explicit inputs, so they
are unit-testable without a database and never touch wall-clock, randomness
or dict-iteration order.

Pinned semantics (planner step 8, with documented resolutions where the pin
left room):

* ``FEATURES_VERSION = '1.0.0'`` — recompute cache key (``STATE_VERSION``
  precedent); threaded through the materialize API into every row.
* POSITIONS: assigned by ascending clockwise seat distance from
  ``button_seat`` (clockwise order is ascending seat number, per
  ``poker.engine.rules.seat_clockwise_order``). The first seat clockwise from
  the button is 'BTN', the second 'SB', the third 'BB'; the ``m = N - 3``
  interior seats (N >= 4): a single interior seat is 'UTG' (4-max), otherwise
  the first interior seat is 'UTG', the last is 'CO' and the interior ones
  are 'MP' (full-ring labels collapse UTG+1..HJ into 'MP' — a documented
  lossy analytics vocabulary). Heads-up (N == 2): the button is also the
  small blind (posts sb) and carries the 'BTN' label; the other seat is 'BB'.
  N == 3 has no interior seats ('BTN', 'SB', 'BB' only).
* POT_TYPES is a per-HAND classification of the preflop betting, derived from
  ALL preflop voluntary actions excluding POST: raise count ``r`` over
  ``street == 0`` (BET or RAISE; a BET cannot legally occur preflop while
  big_blind > 0, so this is defensive) and voluntary call count ``c`` (CALL;
  blind/ante POSTs never count): ``r == 0 and c == 0 -> 'unopened'``;
  ``r == 0 and c >= 1 -> 'limped'``; ``r == 1 -> 'single-raised'``;
  ``r >= 2 -> '3bet+'``. Deliberate resolution: unlike FACING_ACTION this is
  NOT prefix-scoped — "pot type" is a hand-level analytic label
  (limped/SRP/3bet+-pot conventions), identical for every feature row of a
  hand.
* FACING_ACTIONS is per decision: aggressive actions (BET or RAISE — a
  postflop BET is the street's first raise-level, so 'facing-raise'/
  'facing-3bet'/'facing-4bet+' keep their poker meaning) and CALLs on the
  CURRENT street strictly before the decision: 0 raises and 0 calls ->
  'unopened'; 0 raises and >= 1 call -> 'limped'; 1 raise -> 'facing-raise';
  2 raises -> 'facing-3bet'; >= 3 raises -> 'facing-4bet+'.
* SIZING_BUCKETS precedence: CHECK (and FOLD — amountless actions share the
  amountless 'check' bucket; the pinned closed vocabulary has no separate
  fold label, recorded not silent) -> 'check'; preflop CALL of an unraised
  pot (street 0 with 0 raises before it) -> 'limp'; any other CALL -> 'call';
  BET/RAISE with ``delta >= remaining stack`` -> 'all-in' (CALL takes
  precedence over the all-in check, per the pinned precedence order);
  otherwise ``ratio = delta * 1000 // pot_before`` with ``pot_before <= 0``
  failing closed (ValueError): ``<= 600 -> 'small'``; ``<= 1000 ->
  'pot-sized-ish'``; else 'overbet' (600 and 1000 inclusive).
* BOARD_DESCRIPTORS come from board_cards rows (DB-derived, NOT replay —
  the engine's board scope is out of engine scope by design): ``street <= 0
  -> ''``; else the texture of all rows with ``row.street <= street``:
  distinct suits 1/2/3 -> 'monotone'/'two-tone'/'rainbow' (more than 3
  distinct suits fails closed), suffixed '-paired' when any rank is
  duplicated. 'monotone-paired' is therefore unreachable for >= 2 board cards
  (a duplicated rank requires a second suit; the card domain forbids
  identical cards) — the suffix logic stays generic.
* INITIATIVE: 1 iff the last BET/RAISE on the current street earlier in the
  sequence was made by the deciding seat, else 0. STRUCTURAL NOTE: under
  ``StateEngine`` strict replay the last same-street aggressor never faces
  another decision on that street (the aggressor is marked as acted; only a
  later aggression by ANOTHER player re-opens decisions, and that player then
  owns "last aggression"), so every engine-legal decision row carries 0. The
  pinned rule and the 0/1 CHECK remain exactly as specified (verified
  empirically against the engine; the builder stays testable with synthetic
  sequences).
* EFFECTIVE_STACK: the snapshot's remaining chips for the actor, converted
  ``chips * 1000 // big_blind`` (INTEGER permille-of-bb; PS-4 forbids chip
  floats). ``big_blind == 0`` fails closed (ValueError) even though the
  hands CHECK only enforces >= 0.
"""

from __future__ import annotations

from poker.domain.game import ActionKind, Street

__all__ = [
    "FEATURES_VERSION",
    "POSITIONS",
    "POT_TYPES",
    "FACING_ACTIONS",
    "SIZING_BUCKETS",
    "build_positions",
    "build_pot_type",
    "build_facing_action",
    "build_sizing_bucket",
    "build_board_descriptor",
    "build_initiative",
    "build_legal_actions_string",
    "build_effective_stack_milli_bb",
]

FEATURES_VERSION = "1.0.0"
"""Feature-semantics cache key ('1.0.0'); bump on any builder-semantics change."""

POSITIONS = frozenset({"BTN", "SB", "BB", "UTG", "MP", "CO"})
POT_TYPES = frozenset({"unopened", "limped", "single-raised", "3bet+"})
FACING_ACTIONS = frozenset(
    {"unopened", "limped", "facing-raise", "facing-3bet", "facing-4bet+"}
)
SIZING_BUCKETS = frozenset(
    {"check", "limp", "call", "small", "pot-sized-ish", "overbet", "all-in"}
)

_AGGRESSIVE_KINDS = frozenset({ActionKind.BET, ActionKind.RAISE})


def build_positions(seat_numbers, button_seat):
    """Seat -> position label map, ascending clockwise from the button."""
    seats = sorted(int(seat) for seat in seat_numbers)
    if button_seat is None or button_seat not in seats:
        raise ValueError(
            "button seat %r is not among table seats %s" % (button_seat, seats)
        )
    count = len(seats)
    if count < 2:
        raise ValueError("positions require at least 2 seated players, got %d" % count)
    start = seats.index(button_seat)
    clockwise = seats[start:] + seats[:start]
    labels = {}
    if count == 2:  # heads-up pin: BTN = SB (posts sb); the other seat is BB.
        labels[clockwise[0]] = "BTN"
        labels[clockwise[1]] = "BB"
        return labels
    labels[clockwise[0]] = "BTN"
    labels[clockwise[1]] = "SB"
    labels[clockwise[2]] = "BB"
    interior = clockwise[3:]
    m = count - 3
    for offset, seat in enumerate(interior):
        if m == 1 or offset == 0:
            labels[seat] = "UTG"
        elif offset == m - 1:
            labels[seat] = "CO"
        else:
            labels[seat] = "MP"
    unknown = set(labels.values()) - POSITIONS
    if unknown:  # defensive: the construction can only produce known labels
        raise ValueError("position vocabulary violation: %s" % sorted(unknown))
    return labels


def build_pot_type(kinds):
    """Hand-level preflop classification from ALL preflop voluntary kinds."""
    raises = sum(1 for kind in kinds if kind in _AGGRESSIVE_KINDS)
    calls = sum(1 for kind in kinds if kind is ActionKind.CALL)
    if raises == 0:
        return "unopened" if calls == 0 else "limped"
    if raises == 1:
        return "single-raised"
    return "3bet+"


def build_facing_action(raise_count, call_count):
    """Per-decision facing label from same-street raise/call counts."""
    if raise_count == 0:
        return "unopened" if call_count == 0 else "limped"
    if raise_count == 1:
        return "facing-raise"
    if raise_count == 2:
        return "facing-3bet"
    return "facing-4bet+"


def build_sizing_bucket(kind, delta, pot_before, remaining, street, raise_count):
    """Sizing bucket for one chip-bearing or amountless action (precedence)."""
    if kind in (ActionKind.CHECK, ActionKind.FOLD):
        return "check"  # amountless bucket (documented: FOLD shares 'check')
    if kind is ActionKind.CALL:
        if street is Street.PREFLOP and raise_count == 0:
            return "limp"
        return "call"
    if kind in _AGGRESSIVE_KINDS:
        if delta >= remaining:
            return "all-in"
        if pot_before <= 0:
            raise ValueError(
                "sizing bucket requires a positive pot_before (a BET/RAISE into "
                "an empty pot has no defined ratio); got %r" % (pot_before,)
            )
        ratio = delta * 1000 // pot_before
        if ratio <= 600:
            return "small"
        if ratio <= 1000:
            return "pot-sized-ish"
        return "overbet"
    raise ValueError("unsupported action kind for sizing: %r" % (kind,))


def build_board_descriptor(rows, street):
    """Cumulative board texture for rows with ``row.street <= street``.

    ``rows`` is an iterable of ``(street, card)`` pairs (card = rank + suit,
    e.g. 'Ah'); derived from DB rows, never from the replay engine.
    """
    if street <= 0:
        return ""
    included = [str(card) for row_street, card in rows if int(row_street) <= street]
    if not included:
        return ""
    suits = {card[1] for card in included}
    if len(suits) == 1:
        texture = "monotone"
    elif len(suits) == 2:
        texture = "two-tone"
    elif len(suits) == 3:
        texture = "rainbow"
    else:
        raise ValueError(
            "board texture with %d distinct suits is outside the pinned "
            "vocabulary (monotone/two-tone/rainbow)" % len(suits)
        )
    ranks = [card[0] for card in included]
    if len(ranks) != len(set(ranks)):
        texture += "-paired"
    return texture


def build_initiative(street_actions, actor_seat):
    """1 iff the last same-street BET/RAISE strictly earlier in sequence is
    the deciding seat, else 0.

    ``street_actions`` is the ordered list of ``(ActionKind, seat_number)``
    pairs on the CURRENT street strictly before the decision. NOTE: under
    StateEngine strict replay this is always 0 for engine-legal hands (see
    module docstring) — the rule stays as pinned.
    """
    last_aggressive_seat = None
    for kind, seat in street_actions:
        if kind in _AGGRESSIVE_KINDS:
            last_aggressive_seat = seat
    return 1 if last_aggressive_seat == actor_seat else 0


def build_legal_actions_string(kinds):
    """Deterministic sorted comma-joined ActionKind names (e.g. 'BET,CALL,CHECK,RAISE')."""
    return ",".join(sorted(kind.name for kind in kinds))


def build_effective_stack_milli_bb(chips, big_blind):
    """Remaining chips as INTEGER permille-of-big-blind (never a float)."""
    if big_blind <= 0:
        raise ValueError(
            "big_blind must be positive to convert effective_stack_milli_bb "
            "(INTEGER permille-of-bb); got %r" % (big_blind,)
        )
    return chips * 1000 // big_blind
