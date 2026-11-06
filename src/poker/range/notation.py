"""Human-readable range notation for POKER-RANGE-NOTATION-001.

This module defines the repo-canonical textual range grammar and its exact
``fractions.Fraction``-weighted inverse: ``parse`` builds a
``poker.range.model.Range`` from a string; ``format_range`` re-renders a
``Range`` as the canonical string. This module docstring IS the durable
grammar spec (mirroring ``poker.range.model`` rigor); semantics are frozen by
the committed planner handoff (POKER-RANGE-NOTATION-001) and pinned by
``tests/range/test_notation.py`` with in-test oracles only. Any grammar
loosening (umbrella ``'AK'``, bare percentages, ``'0%'`` terms,
percent-of-range) is a new work unit with a new grammar pin — never a silent
change.

Grammar v1
----------
Overall shape:

- ``parse`` accepts a ``str``; anything else raises ``NotationError`` naming
  the offending type. A wholly empty or whitespace-only string raises
  ``NotationError('empty range notation')``.
- The string is split on ``,`` into terms; each term is stripped of its
  surrounding whitespace, so ``'  AKs'`` and ``'AA ,KQo'`` are legal padding.
  A wholly empty term (e.g. from ``'AA,,KQo'``) raises ``NotationError``
  naming its term index. Any other interior whitespace sequence in a term is
  an error (see the suffix rules).
- TERM = CLASS | COMBO, each optionally followed by a percentage suffix.
- Weight semantics: the suffix sets that term's per-combo weight to the
  exact ``Fraction(N, 100)``; a term without a suffix carries
  ``Fraction(1)``. Percentages are PER-COMBO class/combo weights — never
  selection thresholds and never 'N% of a prior range'.

CLASS terms:

- PAIR class: exactly two identical rank characters of ``RANKS``
  (``'23456789TJQKA'``), e.g. ``'AA'``, ``'22'``. Optional ``'+'`` extends
  the pair to every STRONGER pair in A-high order (descending ``RANKS``
  index, A > K > ... > 2): ``'22+'`` is all 13 pairs (78 combos), ``'TT+'``
  is TT..AA inclusive (TT, JJ, QQ, KK, AA — 5 pair classes, 30 combos; the
  committed handoff's '11 pairs' literal is the arithmetically inconsistent
  count for ``'33+'``, not for the described TT..AA span).
- NON-PAIR class: exactly two DISTINCT rank characters (either textual
  order; strength-canonicalized to high > low by ``RANKS`` index, so
  ``'KAo'`` is the same class address as ``'AKo'``) followed by ``'s'``
  (suited) or ``'o'`` (offsuit), e.g. ``'AKs'``, ``'KQo'``. Optional ``'+'``
  extends the LOW rank upward in strength to just below the high rank:
  ``'A2s+'`` = ``{A2s..AKs}`` (12 classes, 48 combos), ``'A2o+'`` = all
  offsuit ace-x (12 classes, 48 combos), ``'AKs+'`` = the single legal
  ``AKs`` class. ``'AK+'`` (plus without a suited/offsuit kind) is invalid;
  a suited/offsuit kind on a pair (``'AAs'``) is invalid; the umbrella form
  ``'AK'`` (no kind) is invalid — write ``AKs`` or ``AKo``.

COMBO terms:

- EXPLICIT COMBO: exactly four ADJACENT characters ``c1 s1 c2 s2`` — rank
  character, suit character, rank character, suit character (suits
  ``'cdhs'``), e.g. ``'AsKd'``; pair combos such as ``'2c2d'`` are legal.
  The space-separated two-card form ``'As Kd'`` is NOT supported in v1.
  Impossible/duplicate card symbols (unknown rank, unknown suit, the same
  card twice) fail closed. Combo identity is UNORDERED (canonicalized to
  (lower deck-index card, higher)), so ``'AsKd'`` and ``'KdAs'`` are the
  same combo.

PERCENTAGE suffix (exact ``' N%'``):

- ``' N%'`` with ``N`` matching ``^[0-9]{1,3}%$`` and integer value in
  ``1..100`` (e.g. ``'KQo 50%'``, ``'AKs 100%'``, ``'A2s+ 25%'``,
  ``'AKs 1%'``).
- ``'0%'`` is REJECTED: a percentage term cannot express zero weight; the
  message redirects to the range model API for zero weights. ``'350%'``,
  ``'3.5%'``, ``'abc%'``, the bare percentage ``'50%'`` (a suffix operator
  without a term), the glued form ``'AKs50%'`` (no space) and any double
  space (``'AKs  50%'``) raise ``NotationError``.

Build and override order (documented, deterministic):

- ``parse`` starts from the zero ``Range()``, then applies class terms LEFT
  TO RIGHT in input order by ``rng = rng.set_weight(combo, w)`` over every
  combo of that class. Class expansion uses a PRIVATE local classifier over
  the same public source of truth
  ``poker.math.combinatorics.enumerate_starting_hands()`` (1,326 combos),
  classified by ``Card`` rank/suit equality; every produced combo is
  validated fail-closed by ``Range.set_weight`` so a derivation bug cannot
  silently corrupt weights, and model-level failures surface wrapped as
  ``NotationError`` naming the term.
- EXPLICIT combo terms are applied AFTER all class terms REGARDLESS of their
  textual position, overriding the class default for exactly that one combo
  (the documented AC3 order; asserted by tests in both positional orders).
- Duplicates fail closed: a duplicate strength-canonicalized class address
  (``'AA,AA'``, ``'AKo,KAo'``, ``'TT+,TT'``) and a duplicate canonical combo
  (``'AsKd,AsKd'``, ``'AsKd,KdAs'``) raise ``NotationError``. Class keys and
  combo keys live in separate namespaces, so a class term plus an explicit
  combo INSIDE that class is legal and is the override mechanism.

Formatter (``format_range(rng) -> str``):

- Fails closed on non-``Range`` input (naming the type) and on the zero
  range (``total_mass() == 0`` -> ``'cannot format a zero range'``).
- Deterministic canonical ordering: pair classes A-first descending strength
  (``AA..22``), then suited classes, then offsuit classes, each group sorted
  by high rank A-first and low rank ascending ``RANKS`` index (``A2s``
  before ``AKs``); explicit combo terms come LAST, ascending by
  ``(deck_index[lo], deck_index[hi])`` of the canonical unordered pair — the
  same order as ``Range.to_dict``. Terms are joined by a single comma with
  no spaces. The function is pure: two calls always yield byte-identical
  output and zero-weight combos are never emitted.
- Per class, over ALL of its combos: if every combo of the class shares
  exactly one weight ``w`` the class is emitted as ``<class token>`` (when
  ``w == 1``, no suffix) or ``<class token> N%`` (when ``100 * w`` is an
  exact integer percentage, e.g. ``'KQo 50%'``). A class whose uniform
  weight is not an exact integer percentage cannot be class-emitted and
  raises ``NotationError`` naming the class token and the exact weight (no
  rounding, no float).
- Otherwise (mixed weights within the class) the class falls back to
  emitting each positive combo as its own explicit term with its own
  ``'<combo> N%'`` suffix (a weight of ``1`` renders as ``' 100%'``); every
  such weight must be an exact integer percentage or ``NotationError`` names
  the affected combo and its exact ``Fraction`` weight.
- Explicit combos are emitted canonically: non-pair combos high-rank card
  first (``'AsKd'``), pair combos lower deck-index card first (``'2c2d'``).

Bijection guarantee: the parseable form space and the formattable weight
space are pinned identical, so for every supported string s
``parse(format_range(parse(s)))`` yields a ``Range`` whose full 1,326-entry
weight table equals ``parse(s)`` under exact ``Fraction`` equality (zeros
included), and ``format_range`` is deterministic (idempotent re-rendering:
``format_range(parse(format_range(parse(s)))) == format_range(parse(s))``).

API surface: ``__all__ = ['parse', 'format_range', 'NotationError']``;
``NotationError`` subclasses ``ValueError`` and carries no extra attributes;
every grammar-level failure raises exactly ``NotationError`` with a message
containing the offending term/token repr and a stable lowercase reason
phrase (tests pin message substrings, never full texts).
"""

from __future__ import annotations

import re
from fractions import Fraction

from poker.domain.cards import DECK, RANKS, SUITS, Card
from poker.math.combinatorics import enumerate_starting_hands
from poker.range.model import Range

__all__ = ["parse", "format_range", "NotationError"]

#: The single allowed regex in the hand parser: the percentage suffix digits.
_SUFFIX_PATTERN = re.compile(r"^[0-9]{1,3}%$")


class NotationError(ValueError):
    """Grammar/wrapper failure of the range notation (a ValueError)."""


# ---------------------------------------------------------------------------
# Private canonical maps and the private class->combos classifier. Built once
# at import time from PUBLIC sources only: DECK order, RANKS order and the
# public enumeration ``enumerate_starting_hands()`` (never a model private).
# Every combo handed to ``Range.set_weight`` is re-validated fail-closed by
# the model, so a classifier derivation bug cannot silently corrupt weights.
# ---------------------------------------------------------------------------

#: DECK order index for canonical unordered-pair identity.
_DECK_INDEX: dict[Card, int] = {card: i for i, card in enumerate(DECK)}

#: RANKS index for strength ordering (A-high = descending index).
_RANK_INDEX: dict[str, int] = {rank: i for i, rank in enumerate(RANKS)}

#: Full-deck canonical combos from the public enumeration source of truth.
_ALL_COMBOS: tuple[tuple[Card, Card], ...] = tuple(enumerate_starting_hands())


def _canonical_pair(a: Card, b: Card) -> tuple[Card, Card]:
    """Canonical unordered combo: DECK index ascending ((lo card, hi card))."""
    if _DECK_INDEX[a] <= _DECK_INDEX[b]:
        return (a, b)
    return (b, a)


def _classify_pair(pair: tuple[Card, Card]) -> tuple[str, str, str]:
    """PRIVATE structural class address for one canonical combo.

    Classification is recomputed locally from the public ``Card.rank`` /
    ``Card.suit`` attributes (never a model private): equal ranks ->
    ``(rank, rank, 'pair')``; otherwise ``(high, low, kind)`` with ranks
    strength-ordered high>low by ``RANKS`` index and ``kind`` ``'suited'``
    on equal suits / ``'offsuit'`` on distinct suits.
    """
    a, b = pair
    if a.rank == b.rank:
        return (a.rank, a.rank, "pair")
    kind = "suited" if a.suit == b.suit else "offsuit"
    high, low = (
        (a.rank, b.rank)
        if _RANK_INDEX[a.rank] > _RANK_INDEX[b.rank]
        else (b.rank, a.rank)
    )
    return (high, low, kind)


def _nonpair_class_ranks(first: str, second: str) -> tuple[str, str]:
    """Strength-canonical ``(high, low)`` for two distinct rank characters."""
    if _RANK_INDEX[first] > _RANK_INDEX[second]:
        return (first, second)
    return (second, first)


def _kind_value(kind_char: str) -> str:
    """Map the textual kind character ('s'/'o') to the structural kind."""
    return "suited" if kind_char == "s" else "offsuit"


def _pair_plus_keys(rank: str) -> list[tuple[str, str, str]]:
    """One pair class plus every stronger pair (A-high, descending index)."""
    start = _RANK_INDEX[rank]
    return [(RANKS[i], RANKS[i], "pair") for i in range(start, len(RANKS))]


def _low_extension_keys(
    high: str, low: str, kind: str
) -> list[tuple[str, str, str]]:
    """One class plus every stronger low rank up to just below the high."""
    high_index = _RANK_INDEX[high]
    low_index = _RANK_INDEX[low]
    return [(high, RANKS[j], kind) for j in range(low_index, high_index)]


#: 169 structural class addresses -> per-class canonical combo lists.
_CLASS_COMBOS: dict[tuple[str, str, str], list[tuple[Card, Card]]] = {}
for _rank in RANKS:
    _CLASS_COMBOS[(_rank, _rank, "pair")] = []
for _index, _high in enumerate(RANKS):
    for _low in RANKS[:_index]:
        _CLASS_COMBOS[(_high, _low, "suited")] = []
        _CLASS_COMBOS[(_high, _low, "offsuit")] = []
for _pair in _ALL_COMBOS:
    _CLASS_COMBOS[_classify_pair(_pair)].append(_pair)
for _combos in _CLASS_COMBOS.values():
    _combos.sort(key=lambda pair: (_DECK_INDEX[pair[0]], _DECK_INDEX[pair[1]]))

#: All 169 class addresses in canonical emission order: pairs A-first
#: descending strength (``AA..22``), then 78 suited and 78 offsuit
#: addresses, each keyed (high, low, kind) with high A-first descending
#: strength and low ascending RANKS index (``A2s`` before ``AKs``).
_CLASS_ORDER: list[tuple[str, str, str]] = []
for _order_index in range(len(RANKS) - 1, -1, -1):
    _CLASS_ORDER.append((RANKS[_order_index], RANKS[_order_index], "pair"))
for _order_kind in ("suited", "offsuit"):
    for _order_high in range(len(RANKS) - 1, 0, -1):
        for _order_low in range(0, _order_high):
            _CLASS_ORDER.append((RANKS[_order_high], RANKS[_order_low], _order_kind))

del _rank, _index, _high, _low, _pair, _combos
del _order_index, _order_kind, _order_high, _order_low


def _split_suffix(term: str) -> tuple[str, Fraction]:
    """Split an already-stripped term into body and per-combo weight.

    The suffix is exactly one ASCII space followed by ``^[0-9]{1,3}%$`` with
    an integer value in ``1..100`` (``Fraction(N, 100)``). ``'0%'`` fails
    closed with a redirect to the range model API for zero weights; any
    other out-of-range suffix raises; a term without a valid suffix carries
    the whole term as body and exact ``Fraction(1)``.
    """
    parts = term.rsplit(" ", 1)
    if len(parts) == 2 and _SUFFIX_PATTERN.fullmatch(parts[1]):
        percent = int(parts[1][:-1])
        if percent == 0:
            raise NotationError(
                f"term {term!r} carries a zero percentage suffix: a "
                "percentage cannot express zero weight; build zero weights "
                "with the range model API (Range()/set_weight) instead"
            )
        if not percent <= 100:
            raise NotationError(
                f"term {term!r} has a percentage suffix outside 1..100: "
                f"{parts[1]!r}"
            )
        return parts[0], Fraction(percent, 100)
    return term, Fraction(1)


def _parse_body_then_wrap(body: str, term: str) -> tuple[str, object]:
    """Parse one term body, re-contexting any body-level failure with the
    FULL raw term repr so every message names the offending term."""
    try:
        return _parse_body(body)
    except NotationError as error:
        raise NotationError(f"invalid range term {term!r}: {error}") from None


def _parse_body(body: str) -> tuple[str, object]:
    """Parse one suffix-stripped term body.

    Returns ``('class', [class addresses...])`` or ``('combo', canonical
    unordered pair)``; every grammar-level violation raises ``NotationError``
    with the offending body repr and a stable lowercase reason phrase.
    """
    if body.endswith("+"):
        stem = body[:-1]
        if len(stem) == 2 and stem[0] == stem[1] and stem[0] in _RANK_INDEX:
            return ("class", _pair_plus_keys(stem[0]))
        if (
            len(stem) == 3
            and stem[2] in ("s", "o")
            and stem[0] in _RANK_INDEX
            and stem[1] in _RANK_INDEX
            and stem[0] != stem[1]
        ):
            high, low = _nonpair_class_ranks(stem[0], stem[1])
            return ("class", _low_extension_keys(high, low, _kind_value(stem[2])))
        raise NotationError(
            f"invalid '+' usage in term {body!r}: '+' extends a pair class "
            "such as '22+' or 'TT+' or a suited/offsuit class such as "
            "'A2s+' or 'AKs+'"
        )
    if len(body) == 2:
        if body[0] == body[1] and body[0] in _RANK_INDEX:
            return ("class", [(body[0], body[0], "pair")])
        if body[0] in _RANK_INDEX and body[1] in _RANK_INDEX and body[0] != body[1]:
            raise NotationError(
                f"term {body!r} is an umbrella class, which is not "
                f"supported: write '{body}s' or '{body}o'"
            )
        raise NotationError(
            f"unknown range term {body!r}: expected a pair class ('AA'), a "
            "suited/offsuit class ('AKs') or a four-character combo ('AsKd')"
        )
    if len(body) == 3:
        kind = body[2]
        if kind not in ("s", "o"):
            raise NotationError(
                f"unknown range term {body!r}: non-pair classes need a 's' "
                "or 'o' kind suffix"
            )
        if body[0] == body[1]:
            raise NotationError(
                f"term {body!r} applies a suited/offsuit kind to a pair: "
                "pairs are written as exactly two equal rank characters "
                "(e.g. 'AA')"
            )
        if body[0] not in _RANK_INDEX or body[1] not in _RANK_INDEX:
            raise NotationError(
                f"unknown rank character in term {body!r}: ranks are "
                f"{''.join(RANKS)!r}"
            )
        high, low = _nonpair_class_ranks(body[0], body[1])
        return ("class", [(high, low, _kind_value(kind))])
    if len(body) == 4:
        return ("combo", _parse_combo(body))
    raise NotationError(
        f"unknown range term {body!r}: expected a pair class ('AA'), a "
        "suited/offsuit class ('AKs') or a four-character combo ('AsKd')"
    )


def _parse_combo(body: str) -> tuple[Card, Card]:
    """Validate a four-character adjacent combo and canonicalize it."""
    first_rank, first_suit, second_rank, second_suit = body
    if (
        first_rank not in _RANK_INDEX
        or second_rank not in _RANK_INDEX
        or first_suit not in SUITS
        or second_suit not in SUITS
    ):
        raise NotationError(
            f"unknown card symbol in combo {body!r}: cards are two-character "
            f"rank+suit symbols (ranks {''.join(RANKS)!r}, suits "
            f"{''.join(SUITS)!r})"
        )
    if first_rank == second_rank and first_suit == second_suit:
        raise NotationError(
            f"combo {body!r} uses the same card twice; a combo is two "
            "distinct cards"
        )
    return _canonical_pair(Card(first_rank, first_suit), Card(second_rank, second_suit))


def parse(text: str) -> Range:
    """Parse a comma-separated range-notation string into an exact Range.

    Grammar v1 per the module docstring: comma-split stripped terms, each an
    optional-percentage CLASS or COMBO term; class terms applied LEFT TO
    RIGHT via ``set_weight`` over their canonical combos, and ALL explicit
    combo terms applied AFTER every class term regardless of textual
    position (the documented AC3 override order). Duplicate
    strength-canonicalized class addresses and duplicate canonical unordered
    combos fail closed in their separate duplicate namespaces.
    """
    if not isinstance(text, str):
        raise NotationError(f"parse expects a str, got {type(text).__name__}")
    if text.strip() == "":
        raise NotationError("empty range notation")
    range_obj = Range()
    seen_class_terms: dict[tuple[str, str, str], str] = {}
    seen_combo_terms: dict[tuple[Card, Card], str] = {}
    explicit_terms: list[tuple[tuple[Card, Card], Fraction, str]] = []
    for index, raw_term in enumerate(text.split(",")):
        term = raw_term.strip()
        if term == "":
            raise NotationError(
                f"empty range term at index {index}: {raw_term!r}"
            )
        body, weight = _split_suffix(term)
        if " " in body:
            if body != body.strip():
                raise NotationError(
                    f"term {term!r} contains whitespace other than the "
                    "single space of a percentage suffix"
                )
            raise NotationError(
                f"term {term!r} uses the unsupported space-separated "
                "two-card form: write a four-character adjacent combo "
                "such as 'AsKd'"
            )
        kind, payload = _parse_body_then_wrap(body, term)
        if kind == "class":
            for key in payload:
                first = seen_class_terms.get(key)
                if first is not None:
                    raise NotationError(
                        f"duplicate class term {body!r} repeats the same "
                        f"strength-canonical address as {first!r}"
                    )
                seen_class_terms[key] = body
            for key in payload:
                for combo in _CLASS_COMBOS[key]:
                    try:
                        range_obj = range_obj.set_weight(combo, weight)
                    except (TypeError, ValueError) as error:
                        raise NotationError(
                            f"term {body!r} failed to set combo weight: {error}"
                        ) from None
        else:
            canonical = payload
            first = seen_combo_terms.get(canonical)
            if first is not None:
                raise NotationError(
                    f"duplicate combo term {body!r} (canonical pair "
                    f"{canonical[0].symbol}/{canonical[1].symbol}) already "
                    f"given as {first!r}"
                )
            seen_combo_terms[canonical] = body
            explicit_terms.append((canonical, weight, body))
    for canonical, weight, body in explicit_terms:
        try:
            range_obj = range_obj.set_weight(canonical, weight)
        except (TypeError, ValueError) as error:
            raise NotationError(
                f"term {body!r} failed to set combo weight: {error}"
            ) from None
    return range_obj


def _class_token(key: tuple[str, str, str]) -> str:
    """Canonical class token text: 'AA' for pairs, otherwise '<high><low><s/o>'."""
    high, low, kind = key
    if kind == "pair":
        return f"{high}{high}"
    return f"{high}{low}{'s' if kind == 'suited' else 'o'}"


def _combo_token(pair: tuple[Card, Card]) -> str:
    """Canonical combo emission text: pair combos render lower deck-index
    card first ('2c2d'); non-pair combos render high-rank card first
    ('AsKd')."""
    low, high = pair
    if low.rank == high.rank:
        first, second = low, high
    elif _RANK_INDEX[high.rank] > _RANK_INDEX[low.rank]:
        first, second = high, low
    else:
        first, second = low, high
    return f"{first.symbol}{second.symbol}"


def format_range(rng: Range) -> str:
    """Render an exact Range as the canonical deterministic notation string.

    Fails closed on non-``Range`` input (naming the type) and on the zero
    range. Canonical ordering: pair classes descending strength, then suited
    classes, then offsuit classes (high rank A-first, low rank ascending
    ``RANKS`` index), then explicit combo terms ascending by
    ``(deck_index[lo], deck_index[hi])`` of the canonical unordered pair,
    matching ``Range.to_dict`` order. Per class: class-uniform classes emit
    the class token (no suffix at weight 1, otherwise an exact integer
    percentage suffix) and any non-exact percentage fails closed naming the
    class/combo and the exact Fraction weight; otherwise every positive
    combo emits as its own ``'<combo> N%'`` term. Zero-weight combos are
    never emitted; the function is pure and deterministic.
    """
    if not isinstance(rng, Range):
        raise NotationError(
            f"format_range expects a poker.range.model.Range, got "
            f"{type(rng).__name__}"
        )
    if rng.total_mass() == 0:
        raise NotationError(
            "cannot format a zero range: zero-weight ranges carry no "
            "notation terms; build weights with the range model API"
        )
    terms: list[str] = []
    explicit_pairs: list[tuple[tuple[Card, Card], int]] = []
    for key in _CLASS_ORDER:
        combos = _CLASS_COMBOS[key]
        weights = [rng.weight(combo) for combo in combos]
        positive = [weight for weight in weights if weight > 0]
        if not positive:
            continue
        token = _class_token(key)
        if len(set(weights)) == 1:
            weight = positive[0]
            percent = 100 * weight
            if percent.denominator != 1:
                raise NotationError(
                    f"class {token!r} has uniform weight {weight} which is "
                    "not an exact integer percentage"
                )
            count = int(percent)
            terms.append(token if weight == 1 else f"{token} {count}%")
        else:
            for combo, weight in zip(combos, weights):
                if weight == 0:
                    continue
                percent = 100 * weight
                if percent.denominator != 1:
                    raise NotationError(
                        f"combo {_combo_token(combo)} of class {token!r} "
                        f"has weight {weight} which is not an exact "
                        "integer percentage"
                    )
                explicit_pairs.append((combo, int(percent)))
    for combo, percent in sorted(
        explicit_pairs,
        key=lambda item: (_DECK_INDEX[item[0][0]], _DECK_INDEX[item[0][1]]),
    ):
        terms.append(f"{_combo_token(combo)} {percent}%")
    return ",".join(terms)

