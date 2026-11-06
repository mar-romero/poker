"""Deterministic, suit-isomorphism-invariant board texture classification.

Task POKER-RANGE-BOARD-001. Public API (frozen by the planner handoff):

- ``BoardTexture``: frozen dataclass with fields ``board_size`` (int, 3/4/5),
  ``rank_multiplicities`` (tuple[int, ...]: the rank multiplicity partition of
  ``board_size``, descending), ``pairedness`` (str in ``PAIREDNESS_STATES``),
  ``suitedness`` (str in ``SUITEDNESS_STATES``), ``max_suit_count`` (int),
  ``one_away_flush_suits`` (int), ``high_rank`` (str), ``high_band`` (str in
  ``HIGH_CARD_BANDS``), ``straight_windows_available`` (int) and
  ``straight_windows_complete`` (int), plus the method ``texture_key()``
  returning one hashable canonical tuple of all fields for stable cache keys.

- ``classify(cards)``: accepts an iterable of exactly 3, 4 or 5 unique ``Card``
  instances (flop/turn/river covered by one street-generic API; ``board_size``
  is the street proxy). Raises ``ValueError`` on wrong count or duplicate
  cards and ``TypeError`` on non-``Card`` items (strict ``type()`` identity).
  Order-independent, deterministic and pure: no floats, no probabilities, no
  randomness, no global state, no caching, no strategic labels.

- ``RunoutDelta``: frozen, card-identity dataclass for one-board-card
  runouts, with fields ``card`` (the runout ``Card`` itself), ``texture_after``
  (a ``BoardTexture`` of board + runout for caller convenience), ``pairs_board``
  (bool: the runout rank already appears on the board), ``completes_flush``
  (bool: the resulting board contains five cards of one suit), ``extends_flush``
  (bool: ``max_suit_count`` strictly increases), ``straight_windows_before`` and
  ``straight_windows_after`` (available-window counts before and after) and
  ``completes_board_straight`` (bool: ``straight_windows_complete`` goes 0->1).

- ``runout_delta(board, runout_card)``: ``board`` must be exactly 3 or 4
  unique ``Card`` instances (river boards have no runout); ``runout_card`` must
  be a ``Card`` not already on the board. Fail-closed mirroring ``classify``.

Vocabularies (closed sets, shared by all streets):
- ``PAIREDNESS_STATES = ('no-pair', 'one-pair', 'two-paired', 'trips',
  'full-house', 'quads')``, injected from the rank multiplicity partition:
  ``{4}`` = quads, ``{3, 2}`` = full-house, ``{3, 1, ...}`` = trips,
  ``{2, 2, ...}`` = two-paired, ``{2, 1, ...}`` = one-pair,
  ``{1, ...}`` = no-pair. Partition existence is street-bounded by the input
  size; the label vocabulary is shared so cells emerge when reachable.
- ``SUITEDNESS_STATES = ('rainbow', 'two-tone', 'monotone')``: monotone iff
  all ``board_size`` cards share one suit; two-tone iff exactly two distinct
  suits are present; rainbow iff three or more suits are present. The fine
  structure is carried by ``max_suit_count`` so ``'rainbow'`` never hides a
  3-flush or 4-flush board.
- ``HIGH_CARD_BANDS = ('low', 'mid', 'high', 'superhigh')`` on the highest
  board strength h: low h <= 9, mid h in {10, 11}, high h in {12, 13},
  superhigh h == 14.

Straight windows: the ten canonical windows are the nine five-strength runs
{2..6}, {3..7}, ..., {10..14} plus the wheel {A, 2, 3, 4, 5}, which is always
counted 5-high, never 14-high. A window is *available* iff at least 3 of its
5 strengths are present on the board (at most 2 missing ranks are always
obtainable from the stub because all four copies of any off-board rank are
live); it is *complete* iff all 5 strengths are present. Complete windows are
impossible below five cards, so ``straight_windows_complete`` is 0 for flops
and turns and lies in {0, 1} for rivers.

Isomorphism rule: every ``BoardTexture`` field is suit-label-free (counts,
partitions, windows, rank bands only), so classification is invariant under
any permutation of the four suits. ``RunoutDelta`` is the deliberate
exception: it names the runout card and is therefore card-identity, not
suit-isomorphism-invariant, so blocker/nut effects are preserved (the
documented spec exception).

Version: ``BOARD_TEXTURE_VERSION = '1.0.0'``. Bump on any vocabulary or
field-semantics change, not on internal refactors.

Implementation: rank and suit counting over canonical ``Card`` identity with
a rank-strength window scan, re-derived here from rank multisets only. This
module deliberately does not import ``evaluator``; hand categories and board
texture stay decoupled, and the evaluator is cross-exercised only as an
independent black-box oracle in the tests.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from poker.domain.cards import RANKS, Card

__all__ = [
    "BOARD_TEXTURE_VERSION",
    "BoardTexture",
    "RunoutDelta",
    "classify",
    "runout_delta",
]

BOARD_TEXTURE_VERSION = "1.0.0"

PAIREDNESS_STATES: tuple[str, ...] = (
    "no-pair",
    "one-pair",
    "two-paired",
    "trips",
    "full-house",
    "quads",
)
SUITEDNESS_STATES: tuple[str, ...] = ("rainbow", "two-tone", "monotone")
HIGH_CARD_BANDS: tuple[str, ...] = ("low", "mid", "high", "superhigh")

# Rank strength: '2'..'A' -> 2..14, derived from the canonical RANKS order.
_RANK_STRENGTH = {rank: index + 2 for index, rank in enumerate(RANKS)}

# The ten canonical straight windows: nine five-strength runs plus the wheel
# {A, 2, 3, 4, 5} counted 5-high (never 14-high).
_STRAIGHT_WINDOWS: tuple[frozenset[int], ...] = tuple(
    frozenset(range(start, start + 5)) for start in range(2, 11)
) + (frozenset((14, 2, 3, 4, 5)),)
assert len(_STRAIGHT_WINDOWS) == 10


@dataclass(frozen=True)
class BoardTexture:
    """Immutable, suit-label-free texture facts of one 3/4/5-card board."""

    board_size: int
    rank_multiplicities: tuple[int, ...]
    pairedness: str
    suitedness: str
    max_suit_count: int
    one_away_flush_suits: int
    high_rank: str
    high_band: str
    straight_windows_available: int
    straight_windows_complete: int

    def texture_key(self) -> tuple:
        """Stable hashable cache key: one canonical tuple of all fields."""
        return (
            self.board_size,
            self.rank_multiplicities,
            self.pairedness,
            self.suitedness,
            self.max_suit_count,
            self.one_away_flush_suits,
            self.high_rank,
            self.high_band,
            self.straight_windows_available,
            self.straight_windows_complete,
        )


@dataclass(frozen=True)
class RunoutDelta:
    """Card-identity transition facts for one board-card runout.

    Deliberately NOT suit-isomorphism-invariant: ``card`` names the runout
    card, preserving blocker/nut effects. Use ``texture_after`` (which obeys
    the BoardTexture isomorphism rule) for label-free comparisons.
    """

    card: Card
    texture_after: BoardTexture
    pairs_board: bool
    completes_flush: bool
    extends_flush: bool
    straight_windows_before: int
    straight_windows_after: int
    completes_board_straight: bool


def _validate_card_list(source, allowed_sizes: tuple[int, ...], api_name: str) -> list[Card]:
    """Fail-closed validation mirroring the evaluator pattern.

    Raises ``ValueError`` for wrong card count or duplicates and ``TypeError``
    for non-``Card`` items (strict ``type()`` identity, so subclasses fail).
    """
    card_list = list(source)
    expected = " or ".join(str(size) for size in allowed_sizes)
    if len(card_list) not in allowed_sizes:
        raise ValueError(
            f"{api_name} requires exactly {expected} cards, got {len(card_list)}"
        )
    for card in card_list:
        if type(card) is not Card:
            raise TypeError(
                f"{api_name} expects Card instances, got {type(card).__name__}"
            )
    symbols = {card.symbol for card in card_list}
    if len(symbols) != len(card_list):
        raise ValueError(f"{api_name} received duplicate cards")
    return card_list


def _pairedness_label(multiplicities: tuple[int, ...]) -> str:
    """Pinned partition -> pairedness-label injection."""
    if 4 in multiplicities:
        return "quads"
    if 3 in multiplicities and 2 in multiplicities:
        return "full-house"
    if 3 in multiplicities:
        return "trips"
    if multiplicities.count(2) >= 2:
        return "two-paired"
    if 2 in multiplicities:
        return "one-pair"
    return "no-pair"


def _high_band_label(high_strength: int) -> str:
    """Pinned highest-strength -> high-band label injection."""
    if high_strength <= 9:
        return "low"
    if high_strength <= 11:
        return "mid"
    if high_strength <= 13:
        return "high"
    return "superhigh"


def _build_texture(card_list: list[Card]) -> BoardTexture:
    """Classify already-validated cards (any order) into a BoardTexture."""
    board_size = len(card_list)
    rank_counts: Counter[int] = Counter(_RANK_STRENGTH[card.rank] for card in card_list)
    multiplicities = tuple(sorted(rank_counts.values(), reverse=True))
    suit_counts: Counter[str] = Counter(card.suit for card in card_list)
    max_suit_count = max(suit_counts.values())
    distinct_suits = len(suit_counts)
    if distinct_suits == 1:
        suitedness = "monotone"
    elif distinct_suits == 2:
        suitedness = "two-tone"
    else:
        suitedness = "rainbow"
    one_away_flush_suits = sum(
        1 for count in suit_counts.values() if count == board_size - 1
    )
    high_strength = max(rank_counts)
    strengths = frozenset(rank_counts)
    straight_windows_available = 0
    straight_windows_complete = 0
    for window in _STRAIGHT_WINDOWS:
        present = len(window & strengths)
        if present >= 3:
            straight_windows_available += 1
        if present == 5:
            straight_windows_complete += 1
    return BoardTexture(
        board_size=board_size,
        rank_multiplicities=multiplicities,
        pairedness=_pairedness_label(multiplicities),
        suitedness=suitedness,
        max_suit_count=max_suit_count,
        one_away_flush_suits=one_away_flush_suits,
        high_rank=RANKS[high_strength - 2],
        high_band=_high_band_label(high_strength),
        straight_windows_available=straight_windows_available,
        straight_windows_complete=straight_windows_complete,
    )


def classify(cards) -> BoardTexture:
    """Classify exactly 3, 4 or 5 unique ``Card`` instances.

    Raises ``ValueError`` for wrong card count (only flop/turn/river sizes)
    or duplicate cards and ``TypeError`` for non-``Card`` items. Pure,
    order-independent and suit-isomorphism-invariant.
    """
    validated = _validate_card_list(cards, (3, 4, 5), "classify")
    return _build_texture(validated)


def runout_delta(board, runout_card) -> RunoutDelta:
    """Compute the single-runout-card transition facts for a flop/turn board.

    ``board`` must be exactly 3 or 4 unique ``Card`` instances (river boards
    have no runout); ``runout_card`` must be a ``Card`` instance not already
    on the board. Fail-closed mirroring ``classify``. Card-identity by
    design: deliberately not suit-isomorphism-invariant.
    """
    validated_board = _validate_card_list(board, (3, 4), "runout_delta board")
    if type(runout_card) is not Card:
        raise TypeError(
            f"runout_delta expects a Card runout, got {type(runout_card).__name__}"
        )
    board_symbols = {card.symbol for card in validated_board}
    if runout_card.symbol in board_symbols:
        raise ValueError(f"runout card {runout_card.symbol!r} is already on the board")

    before = _build_texture(validated_board)
    texture_after = _build_texture(validated_board + [runout_card])
    board_ranks = {card.rank for card in validated_board}
    return RunoutDelta(
        card=runout_card,
        texture_after=texture_after,
        pairs_board=runout_card.rank in board_ranks,
        completes_flush=texture_after.max_suit_count == 5,
        extends_flush=texture_after.max_suit_count > before.max_suit_count,
        straight_windows_before=before.straight_windows_available,
        straight_windows_after=texture_after.straight_windows_available,
        completes_board_straight=(
            before.straight_windows_complete == 0
            and texture_after.straight_windows_complete == 1
        ),
    )

