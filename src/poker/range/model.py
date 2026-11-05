"""Exact weighted range model over the 1,326 canonical deck combos.

A :class:`Range` is a set of exact ``fractions.Fraction`` weights over the
1,326 unordered two-card combos of the canonical 52-card ``DECK`` from
``poker.domain.cards``. Value-object semantics apply: mutating-style members
(:meth:`Range.set_weight`, :meth:`Range.condition`) return a NEW ``Range``
and never touch the receiver. Combos that were never given a weight default
to ``Fraction(0)``: the empty ``Range()`` is a legal zero range and every one
of the 1,326 entries is always present internally, so the observable
invariant ``'impossible combos have zero mass'`` holds directly — conditioning
on dead cards keeps the combo entries and forces their weight to
``Fraction(0)`` (zero, not deleted).

Dead-card concept (single set) and no-float policy are identical to
``poker.math.combinatorics``: board cards and opponent/blocker cards are ONE
``dead_cards`` set, and binary floats and ``Decimal`` are deliberately
excluded from all authoritative paths (weights are only plain ``int`` or
``Fraction``, bounds ``[0, 1]`` inclusive, ``0`` and ``1`` legal). Live-combo
enumeration is delegated to the public
``poker.math.combinatorics.enumerate_starting_hands(dead_cards)``
(never re-enumerated locally) so dead-card filtering has a single source of
truth.

Exact-Fraction-only policy: ``weight``/``total_mass``/``normalized_weights``
and the class masses of ``class_groups`` always return ``Fraction`` values
(int weights are stored as exact ``Fraction``); no float is ever observable.

Canonicalization: every combo (a sequence of exactly two distinct ``Card``
objects) is canonicalized to ``(lower_DECK_INDEX_card, higher)``. Illegal
inputs fail closed: a non-``Card`` element or wrong arity raises
``TypeError``; a duplicate card inside a combo, or any combo that is not
exactly one of the 1,326 canonical pairs, raises ``ValueError``.

Structural classes: ``class_groups()`` returns a dict with exactly 169 keys
``(high_rank, low_rank, kind)`` where ``kind`` is recomputed locally from the
public ``Card.rank``/``Card.suit`` attributes (13 ``'pair'`` classes keyed
``(rank, rank, 'pair')``, 78 ``'suited'`` and 78 ``'offsuit'`` classes keyed
with ranks ordered high>low in ``RANKS`` index order). All 169 keys are
always present, including zero-mass classes, and the class masses sum exactly
to ``total_mass()`` by construction.

Serialization: ``to_dict()``/``from_dict()`` round-trip a versioned,
exact-keys payload of canonical ``Card.symbol`` pairs (lower deck-index card
first) with exact reduced ``str(Fraction)`` weight strings, sorted ascending
by ``(deck_index[lo], deck_index[hi])``; zero-weight combos are excluded from
the listing and every weight value is a string, so no float ever enters
JSON. ``normalized_weights()`` returns a dict over ALL 1,326 canonical keys
(each ``weight / total_mass``; zero-weight combos map to ``Fraction(0)``) and
raises ``ValueError`` fail-closed on the zero range.

Human-readable range notation (``'AA'`` / ``'AKs'`` / ``'AKo'``) is
deliberately deferred to the range-notation work unit
(POKER-RANGE-NOTATION-001); this module operates on ``(Card, Card)`` pairs
only. Downstream blockers, class and notation layers depend on this
module's exact-weights contract (NOTATION depends on normalized presentation,
BLOCKERS on exact unconditioned/conditioned weights, CLASSES on
``class_groups``).

Semantics are frozen by the committed planner handoff for
POKER-RANGE-MODEL-001; the committed test-designer handoff is the test
specification and ``tests/range/test_model.py`` pins every behavior below
with in-test oracles only.
"""

from __future__ import annotations

from fractions import Fraction

from poker.domain import game
from poker.domain.cards import DECK, RANKS, Card, assert_unique_cards
from poker.math.combinatorics import enumerate_starting_hands

__all__ = ["Range"]

# Full-deck canonical map: built once at import time via the delegated public
# enumeration (dead_cards=()); strict DECK i<j order, exactly 1,326 combos.
# ``_ALL_COMBOS`` is read-only by convention and ``Range`` never mutates it.
_DECK_INDEX: dict[Card, int] = {card: i for i, card in enumerate(DECK)}
_ALL_COMBOS: tuple[tuple[Card, Card], ...] = tuple(enumerate_starting_hands())
_CANONICAL_PAIRS: frozenset[tuple[Card, Card]] = frozenset(_ALL_COMBOS)

_RANGE_KEYS = frozenset({"schema_version", "weights"})
_ENTRY_KEYS = frozenset({"cards", "weight"})

#: RANKS index for high>low structural-class key ordering.
_RANK_INDEX: dict[str, int] = {rank: i for i, rank in enumerate(RANKS)}


def _validated_pair(pair: object, label: str = "combo") -> tuple[Card, Card]:
    """Validate a two-Card combo and canonicalize to deck-index ascending.

    Arity and non-``Card`` elements raise ``TypeError``; a duplicate card
    inside the pair or any pair that is not exactly one of the 1,326
    canonical unordered combos raises ``ValueError`` (fail closed).
    """
    try:
        items = list(pair)
    except TypeError:
        raise TypeError(
            f"{label} must be a sequence of two Card objects, "
            f"got {type(pair).__name__}: {pair!r}"
        ) from None
    if len(items) != 2:
        raise TypeError(f"{label} must contain exactly two Card objects, got {len(items)}")
    assert_unique_cards(items)
    a, b = items
    lo, hi = (a, b) if _DECK_INDEX[a] < _DECK_INDEX[b] else (b, a)
    canonical = (lo, hi)
    if canonical not in _CANONICAL_PAIRS:
        raise ValueError(
            f"{label} {lo.symbol}/{hi.symbol} is not one of the 1,326 "
            "canonical two-card combos of the 52-card deck"
        )
    return canonical


def _validated_weight(weight: object, label: str) -> Fraction:
    """Validate a weight (plain ``int`` or ``Fraction``; bounds ``[0, 1]``).

    ``bool``/float/``Decimal``/str/None raise ``TypeError`` (strict identity
    mirroring ``poker.math.combinatorics``); out-of-bounds raise
    ``ValueError``. Integer weights are normalized to exact ``Fraction``.
    """
    if type(weight) is not int and not isinstance(weight, Fraction):
        raise TypeError(
            f"{label} must be an int or a Fraction, "
            f"got {type(weight).__name__}: {weight!r}"
        )
    if weight < 0 or weight > 1:
        raise ValueError(f"{label} must be within [0, 1], got {weight!r}")
    return Fraction(weight)


def _zero_table() -> dict[tuple[Card, Card], Fraction]:
    """A fresh full table over all 1,326 canonical combos, all ``Fraction(0)``."""
    return {combo: Fraction(0) for combo in _ALL_COMBOS}


class Range:
    """Exact weights over the 1,326 unordered combos (value objects).

    Every one of the 1,326 entries is always present with a weight; combos
    never specified default to ``Fraction(0)`` ('impossible combos have zero
    mass' observable invariant). Instances are treated as value objects:
    ``set_weight`` and ``condition`` return NEW instances.
    """

    def __init__(self, combos=None, weights=None):
        """Build a zero range or validate a paired combos/weights intake.

        With both sequences ``None`` the result is the empty zero range.
        Sequence length mismatch raises ``ValueError``; duplicate unordered
        combos in the input raise ``ValueError``; pair-structure violations
        raise ``TypeError``/``ValueError`` per :func:`_validated_pair`; weight
        type/bounds errors per :func:`_validated_weight`.
        """
        if combos is None and weights is None:
            self._weights = _zero_table()
            return
        if combos is None or weights is None:
            raise ValueError(
                "combos and weights must be provided together "
                "as paired sequences"
            )
        try:
            combo_items = list(combos)
            weight_items = list(weights)
        except TypeError as error:
            raise TypeError(
                "combos and weights must be sequences: "
                f"combos={type(combos).__name__}, weights={type(weights).__name__}"
            ) from error
        if len(combo_items) != len(weight_items):
            raise ValueError(
                "combos and weights must have equal length, "
                f"got {len(combo_items)} combos != {len(weight_items)} weights"
            )
        canonical: list[tuple[Card, Card]] = []
        seen: set[tuple[Card, Card]] = set()
        for index, entry in enumerate(combo_items):
            pair = _validated_pair(entry, f"combos[{index}]")
            if pair in seen:
                raise ValueError(
                    f"duplicate unordered combo {pair[0].symbol}/{pair[1].symbol} "
                    f"at position {index}"
                )
            seen.add(pair)
            canonical.append(pair)
        for index, weight in enumerate(weight_items):
            _validated_weight(weight, f"weights[{index}]")
        table = _zero_table()
        for pair, weight in zip(canonical, weight_items, strict=True):
            validated = _validated_weight(weight, "weights[_]")
            table[pair] = validated
        self._weights = table

    @classmethod
    def _from_table(cls, table: dict[tuple[Card, Card], Fraction]) -> "Range":
        """Internal constructor from a fully-validated canonical table.

        Takes a defensive copy; callers must only supply tables covering all
        1,326 canonical combos with exact ``Fraction`` weights.
        """
        instance = cls.__new__(cls)
        instance._weights = dict(table)
        return instance

    @classmethod
    def uniform(cls, dead_cards=()) -> "Range":
        """Uniform range over every live combo; fail closed when none.

        Dead-card validation delegates to ``cards.assert_unique_cards`` and
        live enumeration is delegated to the public
        ``poker.math.combinatorics.enumerate_starting_hands(dead_cards)``
        (never re-enumerated locally). A single dead-card-set concept:
        board + blockers + exposed cards as one ``dead_cards`` set.
        """
        assert_unique_cards(dead_cards)
        live = tuple(enumerate_starting_hands(dead_cards))
        if not live:
            raise ValueError(
                "uniform requires at least one live combo; "
                "the live deck has fewer than two cards, so no range "
                "can be normalized"
            )
        weight = Fraction(1, len(live))
        table = _zero_table()
        for pair in live:
            table[_validated_pair(pair, f"live[{_DECK_INDEX[pair[0]]}]")] = weight
        return cls._from_table(table)

    def weight(self, combo) -> Fraction:
        """Exact weight of a (canonicalized) combo; default ``Fraction(0)``."""
        return self._weights[_validated_pair(combo)]

    def set_weight(self, combo, weight) -> "Range":
        """Return a NEW ``Range`` with ``weight`` set for ``combo``."""
        pair = _validated_pair(combo)
        value = _validated_weight(weight, "weight")
        table = dict(self._weights)
        table[pair] = value
        return self._from_table(table)

    def condition(self, dead_cards) -> "Range":
        """Zero-out (and KEEP) positive-weight entries touching dead cards.

        Dead-card validation mirrors :meth:`uniform` (same single
        dead-card-set concept). Already-zero entries are untouched, so
        repeated application is idempotent; untouched combos keep their
        exact weights.
        """
        assert_unique_cards(dead_cards)
        dead = set(dead_cards)
        table = dict(self._weights)
        for pair, weight in table.items():
            if weight > 0 and (pair[0] in dead or pair[1] in dead):
                table[pair] = Fraction(0)
        return self._from_table(table)

    def total_mass(self) -> Fraction:
        """Exact Fraction sum over all 1,326 entries; zero range -> ``0``."""
        total = Fraction(0)
        for weight in self._weights.values():
            total += weight
        return total

    def normalized_weights(self) -> dict[tuple[Card, Card], Fraction]:
        """All 1,326 canonical pairs mapped to ``weight / total_mass``.

        Fails closed (``ValueError``) on the zero range.
        """
        total = self.total_mass()
        if total == 0:
            raise ValueError(
                "normalized_weights is undefined for a zero-mass range"
            )
        return {combo: weight / total for combo, weight in self._weights.items()}

    def class_groups(self) -> dict[tuple[str, str, str], Fraction]:
        """Exact masses for all 169 structural class keys (zero-filled).

        Classification is recomputed locally from the public ``Card.rank``/
        ``Card.suit`` attributes (never from combinatorics privates): equal
        ranks -> ``(rank, rank, 'pair')``; distinct ranks -> the higher
        ``RANKS``-index rank first with ``'suited'`` when suits are equal
        (4 combos per class) and ``'offsuit'`` otherwise (12 combos per
        class). Mass conservation (sum == ``total_mass()``) holds by
        construction over the full 1,326-entry table.
        """
        groups: dict[tuple[str, str, str], Fraction] = {}
        for rank in RANKS:
            groups[(rank, rank, "pair")] = Fraction(0)
        for index, high in enumerate(RANKS):
            for low in RANKS[:index]:
                groups[(high, low, "suited")] = Fraction(0)
                groups[(high, low, "offsuit")] = Fraction(0)
        for (a, b), weight in self._weights.items():
            if a.rank == b.rank:
                key = (a.rank, a.rank, "pair")
            else:
                if _RANK_INDEX[a.rank] > _RANK_INDEX[b.rank]:
                    high, low = a.rank, b.rank
                else:
                    high, low = b.rank, a.rank
                kind = "suited" if a.suit == b.suit else "offsuit"
                key = (high, low, kind)
            groups[key] += weight
        return groups

    def to_dict(self) -> dict:
        """Versioned exact-keys payload of non-zero canonical weights.

        Entries carry canonical ``Card.symbol`` lists (lower deck-index card
        first) and exact reduced ``str(Fraction)`` weight strings, ordered
        ascending by ``(deck_index[lo], deck_index[hi])``. Zero-weight combos
        are excluded; the zero range serializes to ``'weights': []``.
        """
        entries = []
        for combo in sorted(
            self._weights, key=lambda pair: (_DECK_INDEX[pair[0]], _DECK_INDEX[pair[1]])
        ):
            weight = self._weights[combo]
            if weight != 0:
                entries.append(
                    {
                        "cards": [combo[0].symbol, combo[1].symbol],
                        "weight": str(weight),
                    }
                )
        return {"schema_version": game.SCHEMA_VERSION, "weights": entries}

    @classmethod
    def from_dict(cls, payload) -> "Range":
        """Fail-closed inverse of :meth:`to_dict` (exact Fraction weights).

        Outer and per-entry exact-keys payloads, strict two-character symbol
        strings validated before ``Card`` construction, weight strings
        parsed with ``Fraction`` (malformed strings propagate its
        ``ValueError``), bounds ``[0, 1]`` enforced, unordered duplicate
        combos rejected. The empty ``weights`` list is the zero range.
        """
        game.require_schema_version(payload)
        game.require_exact_keys(payload, _RANGE_KEYS, "Range")
        weight_entries = payload["weights"]
        if not isinstance(weight_entries, list):
            raise TypeError("Range payload 'weights' must be a list")
        pairs: list[tuple[Card, Card]] = []
        parsed: list[Fraction] = []
        seen: set[tuple[Card, Card]] = set()
        for index, entry in enumerate(weight_entries):
            game.require_exact_keys(entry, _ENTRY_KEYS, f"Range weights[{index}]")
            cards = entry["cards"]
            if not isinstance(cards, list):
                raise TypeError(
                    f"Range weights[{index}].cards must be a list, "
                    f"got {type(cards).__name__}"
                )
            if len(cards) != 2:
                raise ValueError(
                    f"Range weights[{index}].cards must contain exactly two "
                    f"two-character Card symbols, got {len(cards)}"
                )
            card_items = []
            for position, symbol in enumerate(cards):
                if type(symbol) is not str:
                    raise TypeError(
                        f"Range weights[{index}].cards[{position}] must be a "
                        f"str, got {type(symbol).__name__}"
                    )
                if len(symbol) != 2:
                    raise ValueError(
                        f"Range weights[{index}].cards[{position}] must be a "
                        f"two-character Card symbol, got {symbol!r}"
                    )
                card_items.append(Card(symbol[0], symbol[1]))
            pair = _validated_pair(tuple(card_items), f"Range weights[{index}].cards")
            if pair in seen:
                raise ValueError(
                    f"duplicate unordered combo {pair[0].symbol}/{pair[1].symbol} "
                    f"at weights index {index}"
                )
            seen.add(pair)
            raw_weight = entry["weight"]
            if type(raw_weight) is not str:
                raise TypeError(
                    f"Range weights[{index}].weight must be a str, "
                    f"got {type(raw_weight).__name__}"
                )
            weight = _validated_weight(Fraction(raw_weight), f"Range weights[{index}].weight")
            pairs.append(pair)
            parsed.append(weight)
        table = _zero_table()
        for pair, weight in zip(pairs, parsed, strict=True):
            table[pair] = weight
        return cls._from_table(table)
