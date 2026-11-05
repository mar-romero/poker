"""Range-notation tests for POKER-RANGE-NOTATION-001 (cases C01..C32).

Implements the committed test-designer handoff cases verbatim in intent.
Blueprint style mirrors ``tests/range/test_model.py``: oracles entirely
in-test (membership over ``itertools.combinations(DECK, 2)``, deck-index
canonicalization from the committed ``DECK`` order, an independent
structural classifier built from public ``Card`` rank/suit attributes and
the public ``enumerate_starting_hands()`` enumeration source); full-table
comparators over ALL 1,326 combos including zeros; exact ``Fraction``
expectations with no float anywhere.

Grammar-level failures are asserted with ``assertRaises(NotationError)``
(the pinned failure class, always a ``ValueError``) plus stable lowercase
message substrings containing the offending term repr; the
``VALIDATION_ERRORS = (ValueError, TypeError)`` type-in-tuple convention is
retained only where the planner deliberately left the concrete wrap
unpinned (C23 non-str input). All parse/format_range calls happen inside
test methods, so a ``NotImplementedError`` skeleton fails each test at its
first behavioral assertion (behavioral RED with zero collection errors).

Designer-literal deviations (both documented, both forced by more
authoritative pins in the same handoff):
- C13/C14 use ``KdQs`` / ``AsKs`` for the in-class override combo: the
  designer literals used ``AsKd``, which is AK OFFSUIT — provably outside
  both ``KQo`` and ``AKs`` (``AsKd`` = spades+diamonds, distinct suits),
  so it cannot be the 12th KQo combo or the 4th AKs combo. C28's own
  exact-string pin (``'AsKs 30%'`` among the AKs combos) pins ``AsKs`` as
  the intended combo and keeps C13's '11 remaining' and C14's
  'other 3 AKs combos' arithmetic true.
- C25's class sequence emission and C27's explicit-combo ordering follow
  the pinned ascending ``(deck_index[lo], deck_index[hi])`` key
  (``Range.to_dict`` order). Each uniform-percentage class must carry its
  own suffix (otherwise re-parse would reset earlier classes to ``1``,
  breaking the AC1 bijection that C31 itself requires on the C24..C28
  outputs), and ``SUITS = 'cdhs'`` sorts ``h`` before ``s``, so
  ``AhKd`` (key (Kd,Ah) = (45,49)) precedes ``AsKd`` (key (Kd,As) =
  (45,51)) — exactly the ordering its own C28 string uses
  (``AcKc < AdKd < AhKh < AsKs``).
- C26/C27 exact strings carry ' N%' on explicit combo terms (e.g.
  'AsKd 100%'): the planner formatter pin requires every explicit combo
  term to carry 'its own percent suffix' and the designer C28 exact string
  confirms it (``'AcKc 100%,...'``); the bare 'AsKd' / 'AhKd,AsKd' literals
  in C26/C27 contradict that pin exactly as the C25 single-shared-suffix
  literal did.
"""

from __future__ import annotations

import itertools
import unittest
from fractions import Fraction

from poker.domain.cards import DECK, RANKS, Card
from poker.math.combinatorics import enumerate_starting_hands
from poker.range import notation
from poker.range.model import Range
from poker.range.notation import NotationError, format_range, parse

VALIDATION_ERRORS = (ValueError, TypeError)

# ---------------------------------------------------------------------------
# In-test oracles (never importing notation or range-model private helpers).
# ---------------------------------------------------------------------------

#: ORACLE-DISTINCT-1: unfiltered DECK combinations, DECK i<j order
#: (exactly 1,326 unordered pairs).
_ORACLE_ALL_PAIRS: list[tuple[Card, Card]] = list(itertools.combinations(DECK, 2))

#: ORACLE-DISTINCT-2: public enumeration source of truth (dead_cards=()).
_ORACLE_ENUMERATION: tuple[tuple[Card, Card], ...] = tuple(enumerate_starting_hands())

#: DECK symbol -> index, for strict i<j canonical-order pins.
_SYMBOL_INDEX: dict[str, int] = {card.symbol: i for i, card in enumerate(DECK)}

#: RANKS index, for strength-canonicalized class keys and A-high ordering.
_RANK_INDEX: dict[str, int] = {rank: i for i, rank in enumerate(RANKS)}


def _oracle_class_key(a: Card, b: Card) -> tuple[str, str, str]:
    """ORACLE structural class key (independent rank/suit classification)."""
    if a.rank == b.rank:
        return (a.rank, a.rank, "pair")
    kind = "suited" if a.suit == b.suit else "offsuit"
    high, low = sorted((a.rank, b.rank), key=_RANK_INDEX.__getitem__, reverse=True)
    return (high, low, kind)


def _canon_pair(a: Card, b: Card) -> tuple[Card, Card]:
    """ORACLE canonical unordered pair: deck index ascending."""
    return tuple(sorted((a, b), key=lambda card: _SYMBOL_INDEX[card.symbol]))


def _combo(*symbols: str) -> tuple[Card, Card]:
    """ORACLE canonical pair from textual two-character card symbols."""
    cards = [Card(symbol[0], symbol[1]) for symbol in symbols]
    return _canon_pair(cards[0], cards[1])


# The 169 structural class keys -> canonical combos; combo candidates come
# ONLY from the public enumeration, classified in-test by rank/suit equality.
_ORACLE_CLASS_COMBOS: dict[tuple[str, str, str], list[tuple[Card, Card]]] = {}
for _rank in RANKS:
    _ORACLE_CLASS_COMBOS[(_rank, _rank, "pair")] = []
for _index, _high in enumerate(RANKS):
    for _low in RANKS[:_index]:
        _ORACLE_CLASS_COMBOS[(_high, _low, "suited")] = []
        _ORACLE_CLASS_COMBOS[(_high, _low, "offsuit")] = []
for _pair in _ORACLE_ENUMERATION:
    _ORACLE_CLASS_COMBOS[_oracle_class_key(*_pair)].append(_pair)
for _combos in _ORACLE_CLASS_COMBOS.values():
    _combos.sort(
        key=lambda pair: (_SYMBOL_INDEX[pair[0].symbol], _SYMBOL_INDEX[pair[1].symbol])
    )

del _rank, _index, _high, _low, _pair, _combos


def _range_table(range_obj) -> dict[tuple[Card, Card], Fraction]:
    """Full 1,326-entry per-combo weight table (zeros included)."""
    return {pair: range_obj.weight(pair) for pair in _ORACLE_ALL_PAIRS}


def _expected_table(
    class_terms: list[tuple[tuple[str, str, str], Fraction]],
    explicit: list[tuple[tuple[Card, Card], Fraction]] | None = None,
) -> dict[tuple[Card, Card], Fraction]:
    """Independent expected table: class terms FIRST, then explicit combos
    (mirroring the documented AC3 override order)."""
    table = {pair: Fraction(0) for pair in _ORACLE_ALL_PAIRS}
    for key, weight in class_terms:
        for combo in _ORACLE_CLASS_COMBOS[key]:
            table[combo] = weight
    for combo, weight in explicit or []:
        table[combo] = weight
    return table


def _pair_keys_from(rank: str) -> list[tuple[str, str, str]]:
    """A pair class plus every stronger pair (A-high = descending index)."""
    start = _RANK_INDEX[rank]
    return [(RANKS[i], RANKS[i], "pair") for i in range(start, len(RANKS))]


def _nonpair_plus_keys(
    high: str, low: str, kind: str
) -> list[tuple[str, str, str]]:
    """A non-pair class plus every stronger low rank (just-below-high)."""
    hi = _RANK_INDEX[high]
    lo = _RANK_INDEX[low]
    return [(high, RANKS[j], kind) for j in range(lo, hi)]


def _class_combos(key: tuple[str, str, str]) -> list[tuple[Card, Card]]:
    """ORACLE combo list for one structural class key."""
    return _ORACLE_CLASS_COMBOS[key]


def _class_token(key: tuple[str, str, str]) -> str:
    """ORACLE class token text: 'AA' pairs, 'AK' + s/o otherwise."""
    high, low, kind = key
    if kind == "pair":
        return f"{high}{high}"
    return f"{high}{low}{'s' if kind == 'suited' else 'o'}"


def _corpus_strings() -> list[str]:
    """Deterministic (seed-free) supported-string corpus for B10/C31."""
    strings: list[str] = []
    class_tokens = ["AA", "22", "AKs", "KQo", "A2s+", "A2o+", "22+", "AKs+"]
    suffixes = [None, " 1%", " 25%", " 50%", " 100%"]
    for token in class_tokens:
        for suffix in suffixes:
            strings.append(token if suffix is None else f"{token}{suffix}")
    for token in ["AsKd", "AhKd", "2c2d", "KdQs", "KdQs 40%", "AsKs 30%"]:
        strings.append(token)
    strings.extend(
        [
            "22+, AsKd, KQo 50%",
            "AsKd, KQo 50%, 22+",
            "KQo, KdQs 40%",
            "KdQs 40%, KQo",
            "KQo, KdQs 40%, KhQc 40%",
            "KhQc 40%, KQo, KdQs 40%",
            "AKs, AsKs 30%",
            "AsKs 30%, AKs",
            # canonical formatter outputs themselves (C24..C28 surfaces)
            "AA,KK,QQ,JJ,TT,99,88,77,66,55,44,33,22,KQo 50%,AsKd 100%",
            "AhKd 100%,AsKd 100%",
            "AcKc 100%,AdKd 100%,AhKh 100%,AsKs 30%",
            "AKs",
            "KQo 50%",
            "A2s 25%,A3s 25%,A4s 25%,A5s 25%,A6s 25%,A7s 25%,A8s 25%,"
            "A9s 25%,ATs 25%,AJs 25%,AQs 25%,AKs 25%",
        ]
    )
    return strings


_CORPUS: list[str] = _corpus_strings()


# ---------------------------------------------------------------------------
# Shared parse-failure assertion helper (asserts exactly NotationError and
# pins stable lowercase message substrings, never full message texts).
# ---------------------------------------------------------------------------


class _ParseAssertions:
    """Mixin: assert parse() raises exactly NotationError with substrings."""

    def _assert_parse_error(self, text: str, *needles: str) -> NotationError:
        try:
            parse(text)
        except NotationError as error:
            message = str(error)
            for needle in needles:
                self.assertIn(needle, message)
            return error
        self.fail(f"parse({text!r}) did not raise NotationError")
        raise AssertionError  # unreachable; satisfies control-flow checkers


# ---------------------------------------------------------------------------
# C01..C23 — parser behaviors
# ---------------------------------------------------------------------------


class NotationParseTests(_ParseAssertions, unittest.TestCase):
    """C01..C23: parse() grammar, weights, overrides and error matrix."""

    def test_c01_pair_AA_full_table(self) -> None:
        """C01: parse('AA') — exactly the 6 AA combos at 1, rest of the
        full 1,326 table at 0."""
        table = _range_table(parse("AA"))
        self.assertEqual(
            _expected_table([(("A", "A", "pair"), Fraction(1))]), table
        )
        positives = sum(1 for weight in table.values() if weight > 0)
        self.assertEqual(6, positives)

    def test_c02_pair_22_bottom_rank(self) -> None:
        """C02: parse('22') — the 6 deuce pair combos at 1 (bottom-rank
        boundary class), rest 0."""
        table = _range_table(parse("22"))
        self.assertEqual(
            _expected_table([(("2", "2", "pair"), Fraction(1))]), table
        )
        positives = sum(1 for weight in table.values() if weight > 0)
        self.assertEqual(6, positives)

    def test_c03_TT_plus_pairs_TT_through_AA(self) -> None:
        """C03: parse('TT+') — the pair classes TT..AA inclusive
        (TT, JJ, QQ, KK, AA). Designer-literal correction: the handoff's
        '11 pairs / 66 combos' count is arithmetically impossible for the
        explicitly described span TT..AA (that count belongs to '33+');
        TT..AA is exactly 5 pair classes = 30 combos, matching both the
        planner's own '22+' arithmetic rule (13 pairs) and standard poker
        '+' convention."""
        keys = _pair_keys_from("T")
        self.assertEqual(5, len(keys))
        table = _range_table(parse("TT+"))
        self.assertEqual(
            _expected_table([(key, Fraction(1)) for key in keys]), table
        )
        positives = sum(1 for weight in table.values() if weight > 0)
        self.assertEqual(30, positives)
        for rank in ("T", "J", "Q", "K", "A"):
            with self.subTest(pair_rank=rank):
                for combo in _class_combos((rank, rank, "pair")):
                    self.assertEqual(Fraction(1), table[combo])

    def test_c04_22_plus_all_thirteen_pairs(self) -> None:
        """C04: parse('22+') — all 13 pair classes: exactly 78 combos at 1,
        all 1,248 others at 0."""
        keys = _pair_keys_from("2")
        self.assertEqual(13, len(keys))
        table = _range_table(parse("22+"))
        self.assertEqual(
            _expected_table([(key, Fraction(1)) for key in keys]), table
        )
        positives = sum(1 for weight in table.values() if weight > 0)
        self.assertEqual(78, positives)
        self.assertEqual(1248, sum(1 for weight in table.values() if weight == 0))

    def test_c05_AKs_and_A2s_A2o_plus_subtests(self) -> None:
        """C05: subTest full tables for 'AKs' (4 combos), 'A2s+' (12 suited
        classes / 48 combos) and 'A2o+' (12 offsuit classes / 48 combos);
        every other combo 0 in each case."""
        cases = (
            ("AKs", [("A", "K", "suited")]),
            ("A2s+", _nonpair_plus_keys("A", "2", "suited")),
            ("A2o+", _nonpair_plus_keys("A", "2", "offsuit")),
        )
        for text, keys in cases:
            with self.subTest(text=text, classes=len(keys)):
                table = _range_table(parse(text))
                self.assertEqual(
                    _expected_table([(key, Fraction(1)) for key in keys]), table
                )
                expected_positives = sum(
                    len(_class_combos(key)) for key in keys
                )
                positives = sum(1 for weight in table.values() if weight > 0)
                self.assertEqual(expected_positives, positives)

    def test_c06_AKs_plus_singleton_is_AKs(self) -> None:
        """C06: parse('AKs+') table-equal parse('AKs') over all 1,326
        (legal singleton '+' no-op)."""
        self.assertEqual(
            _range_table(parse("AKs")), _range_table(parse("AKs+"))
        )

    def test_c07_KAo_canonicalizes_to_AKo(self) -> None:
        """C07: 'KAo' is the same strength-canonical class address as 'AKo'
        over the full table, with and without a 50% suffix."""
        self.assertEqual(
            _range_table(parse("AKo")), _range_table(parse("KAo"))
        )
        self.assertEqual(
            _range_table(parse("AKo 50%")), _range_table(parse("KAo 50%"))
        )

    def test_c08_KQo_half_is_per_combo(self) -> None:
        """C08: parse('KQo 50%') — all 12 offsuit KQ combos at exactly
        Fraction(1,2) (per-combo weights, not a selection threshold)."""
        key = ("K", "Q", "offsuit")
        table = _range_table(parse("KQo 50%"))
        self.assertEqual(_expected_table([(key, Fraction(1, 2))]), table)
        for combo in _class_combos(key):
            self.assertEqual(Fraction(1, 2), table[combo])

    def test_c09_A2s_plus_quarter(self) -> None:
        """C09: parse('A2s+ 25%') — 48 combos at exactly Fraction(1,4)."""
        keys = _nonpair_plus_keys("A", "2", "suited")
        table = _range_table(parse("A2s+ 25%"))
        self.assertEqual(
            _expected_table([(key, Fraction(1, 4)) for key in keys]), table
        )
        positives = sum(1 for weight in table.values() if weight > 0)
        self.assertEqual(48, positives)

    def test_c10_suffix_bounds(self) -> None:
        """C10: 'AKs 100%' table == 'AKs' table; 'AKs 1%' gives the 4 combos
        exactly Fraction(1,100)."""
        self.assertEqual(
            _range_table(parse("AKs")), _range_table(parse("AKs 100%"))
        )
        key = ("A", "K", "suited")
        table = _range_table(parse("AKs 1%"))
        self.assertEqual(_expected_table([(key, Fraction(1, 100))]), table)
        for combo in _class_combos(key):
            self.assertEqual(Fraction(1, 100), table[combo])

    def test_c11_explicit_combos(self) -> None:
        """C11: parse('AsKd') — single combo at 1, whole table otherwise 0;
        parse('2c2d') — legal explicit pair combo."""
        suit_combo = _combo("As", "Kd")
        table = _range_table(parse("AsKd"))
        self.assertEqual(_expected_table([], explicit=[(suit_combo, Fraction(1))]), table)
        self.assertEqual(1, sum(1 for weight in table.values() if weight > 0))
        pair_combo = _combo("2c", "2d")
        self.assertEqual(
            _expected_table([], explicit=[(pair_combo, Fraction(1))]),
            _range_table(parse("2c2d")),
        )

    def test_c12_mixed_terms_and_shuffled_orders(self) -> None:
        """C12: '22+, AsKd, KQo 50%' — 78 pair combos w=1, AsKd w=1, 12 KQo
        combos w=1/2, rest 0; every term permutation yields the identical
        table."""
        class_terms = [
            (("K", "Q", "offsuit"), Fraction(1, 2)),
        ] + [(key, Fraction(1)) for key in _pair_keys_from("2")]
        expected = _expected_table(
            class_terms, explicit=[(_combo("As", "Kd"), Fraction(1))]
        )
        base = _range_table(parse("22+, AsKd, KQo 50%"))
        self.assertEqual(expected, base)
        positives = sum(1 for weight in base.values() if weight > 0)
        self.assertEqual(91, positives)
        for order in itertools.permutations(("22+", "AsKd", "KQo 50%")):
            with self.subTest(order=order):
                self.assertEqual(
                    base, _range_table(parse(", ".join(order)))
                )

    def test_c13_explicit_combo_inside_class_both_orders(self) -> None:
        """C13: KQo plus an explicit KQo combo ('KQo, KdQs' vs 'KdQs, KQo')
        yields identical full tables; class keys and combo keys are separate
        duplicate namespaces."""
        combo = _combo("Kd", "Qs")
        expected = _expected_table(
            [(("K", "Q", "offsuit"), Fraction(1))], explicit=[(combo, Fraction(1))]
        )
        first = _range_table(parse("KQo, KdQs"))
        second = _range_table(parse("KdQs, KQo"))
        self.assertEqual(expected, first)
        self.assertEqual(first, second)
        for inner_combo in _class_combos(("K", "Q", "offsuit")):
            self.assertEqual(Fraction(1), first[inner_combo])

    def test_c14_suffix_override_inside_class_both_orders(self) -> None:
        """C14: ('AKs', 'AsKs 30%') in both positional orders — identical
        full tables; AsKs = Fraction(3,10), other 3 AKs combos = 1, rest 0."""
        combo = _combo("As", "Ks")  # inside AKs (suited)
        expected = _expected_table(
            [(("A", "K", "suited"), Fraction(1))],
            explicit=[(combo, Fraction(3, 10))],
        )
        first = _range_table(parse("AKs, AsKs 30%"))
        second = _range_table(parse("AsKs 30%, AKs"))
        self.assertEqual(expected, first)
        self.assertEqual(first, second)
        self.assertEqual(Fraction(3, 10), first[combo])
        for other in _class_combos(("A", "K", "suited")):
            if other != combo:
                self.assertEqual(Fraction(1), first[other])

    def test_c15_two_explicit_combos_inside_class_both_orders(self) -> None:
        """C15: two explicit combos inside one class ('KQo, KdQs 40%,
        KhQc 40%') — explicit wins per combo, remaining 10 class combos keep
        the class weight; identical table across all three textual orders."""
        overridden = (_combo("Kd", "Qs"), _combo("Kh", "Qc"))
        expected = _expected_table(
            [(("K", "Q", "offsuit"), Fraction(1))],
            explicit=[(overridden[0], Fraction(2, 5)), (overridden[1], Fraction(2, 5))],
        )
        texts = (
            "KQo, KdQs 40%, KhQc 40%",
            "KdQs 40%, KQo, KhQc 40%",
            "KhQc 40%, KdQs 40%, KQo",
        )
        for text in texts:
            with self.subTest(text=text):
                table = _range_table(parse(text))
                self.assertEqual(expected, table)
                for inner_combo in _class_combos(("K", "Q", "offsuit")):
                    if inner_combo in overridden:
                        self.assertEqual(Fraction(2, 5), table[inner_combo])
                    else:
                        self.assertEqual(Fraction(1), table[inner_combo])

    def test_c16_grammar_error_matrix(self) -> None:
        """C16: arity/unknown char/space form/bogus plus/pair-with-kind/
        missing-kind rejections, each exactly NotationError whose message
        contains the offending term repr."""
        cases = (
            ("A", "'A'"),
            ("AKss", "'AKss'"),
            ("As Kd", "'As Kd'"),
            ("1X", "'1X'"),
            ("10h", "'10h'"),
            ("AK+", "'AK+'"),
            ("+", "'+'"),
            ("A+", "'A+'"),
            ("AAs", "'AAs'"),
            ("5d", "'5d'"),
        )
        for text, needle in cases:
            with self.subTest(text=text):
                self._assert_parse_error(text, needle)
                self._assert_class_error_is_value_error(text)

    def _assert_class_error_is_value_error(self, text: str) -> None:
        """The pinned grammar-failure type must be a ValueError subclass."""
        try:
            parse(text)
        except NotationError as error:
            self.assertIsInstance(error, ValueError)

    def test_c17_suffix_error_matrix(self) -> None:
        """C17: glued ('AKs50%'), out-of-range, non-integer, non-digit,
        double-space ('AKs  50%' — extra space defeats the suffix), two-token
        ('50 50%') and bare-percentage ('50%') rejections; message contains
        the offending term repr."""
        cases = (
            ("AKs50%", "'AKs50%'"),
            ("350%", "'350%'"),
            ("3.5%", "'3.5%'"),
            ("abc%", "'abc%'"),
            ("AKs  50%", "'AKs  50%'"),
            ("50 50%", "'50 50%'"),
            ("50%", "'50%'"),
        )
        for text, needle in cases:
            with self.subTest(text=text):
                self._assert_parse_error(text, needle)

    def test_c18_zero_percent_redirects_to_model_api(self) -> None:
        """C18: parse('KQo 0%') — NotationError whose lowercase message
        contains a redirect phrase mentioning the range model API for zero
        weights (substring pins, not full text)."""
        error = self._assert_parse_error("KQo 0%", "'KQo 0%'")
        message = str(error)
        self.assertIn("zero", message)
        self.assertIn("model", message)

    def test_c19_empty_terms(self) -> None:
        """C19: whole-empty input and each wholly empty term are rejected
        with 'empty' phrasing; middle terms name their term index."""
        for whole in ("", "   "):
            with self.subTest(whole=whole):
                with self.assertRaises(NotationError) as manager:
                    parse(whole)
                self.assertEqual("empty range notation", str(manager.exception))
        with self.assertRaises(NotationError) as manager:
            parse(",,")
        message = str(manager.exception)
        self.assertIn("empty", message)
        self.assertIn("index 0", message)
        with self.assertRaises(NotationError) as manager:
            parse("AA,,KQo")
        message = str(manager.exception)
        self.assertIn("empty", message)
        self.assertIn("index 1", message)

    def test_c20_duplicate_class_terms(self) -> None:
        """C20: duplicate class addresses — exact 'AA,AA' and the
        strength-canonicalization collisions 'AKo,KAo' and 'TT+,TT' — are
        NotationError."""
        cases = (
            ("AA,AA", "duplicate"),
            ("AKo,KAo", "KAo"),
            ("TT+,TT", "TT"),
        )
        for text, needle in cases:
            with self.subTest(text=text):
                error = self._assert_parse_error(text)
                self.assertIn("duplicate", str(error))
                self.assertIn(needle, str(error))

    def test_c21_duplicate_canonical_combos(self) -> None:
        """C21: duplicate canonical unordered combos — identical text
        'AsKd,AsKd' and reordered 'AsKd,KdAs' — are NotationError."""
        cases = (("AsKd,AsKd", "AsKd"), ("AsKd,KdAs", "KdAs"))
        for text, needle in cases:
            with self.subTest(text=text):
                error = self._assert_parse_error(text)
                self.assertIn("duplicate", str(error))
                self.assertIn(needle, str(error))

    def test_c22_impossible_cards(self) -> None:
        """C22: unknown rank, unknown suit and duplicate-card combos fail
        closed, naming the offending combo."""
        cases = (
            ("XsKd", "'XsKd'"),
            ("AsXx", "'AsXx'"),
            ("AsAs", "'AsAs'"),
            ("AsKz", "'AsKz'"),
        )
        for text, needle in cases:
            with self.subTest(text=text):
                self._assert_parse_error(text, needle)

    def test_c23_non_string_input(self) -> None:
        """C23: non-str input raises within the VALIDATION_ERRORS family;
        no unrelated exception type leaks."""
        for value in (None, 3):
            with self.subTest(value=value):
                with self.assertRaises(VALIDATION_ERRORS):
                    parse(value)


# ---------------------------------------------------------------------------
# C24..C32 — formatter behaviors
# ---------------------------------------------------------------------------


class NotationFormatTests(_ParseAssertions, unittest.TestCase):
    """C24..C32: format_range() canonical emission, fail-closed formatting
    and the round-trip bijection property over the full 1,326 table."""

    def test_c24_uniform_class_without_suffix(self) -> None:
        """C24: format_range(parse('AKs')) == 'AKs' exactly (class-
        uniform weight 1 emits no suffix)."""
        self.assertEqual("AKs", format_range(parse("AKs")))

    def test_c25_uniform_classes_with_suffix(self) -> None:
        """C25: canonical emission with exact integer-percent suffixes:
        'KQo 50%' single class; every uniform class carries its own suffix
        (the exact-string pin follows the pinned ascending class ordering;
        a single shared suffix would break the AC1 bijection C31 itself
        requires)."""
        self.assertEqual("KQo 50%", format_range(parse("KQo 50%")))
        self.assertEqual(
            "A2s 25%,A3s 25%,A4s 25%,A5s 25%,A6s 25%,A7s 25%,A8s 25%,"
            "A9s 25%,ATs 25%,AJs 25%,AQs 25%,AKs 25%",
            format_range(parse("A2s+ 25%")),
        )

    def test_c26_canonical_full_order_and_determinism(self) -> None:
        """C26: canonical ordering — pairs 'AA..22' then suited (none) then
        'KQo 50%' then the explicit 'AsKd' last; two calls byte-identical;
        parse(format(...)) reproduces the exact C12 table."""
        text = "22+, AsKd, KQo 50%"
        out = format_range(parse(text))
        self.assertEqual(
            "AA,KK,QQ,JJ,TT,99,88,77,66,55,44,33,22,KQo 50%,AsKd 100%", out
        )
        self.assertEqual(out, format_range(parse(text)))
        self.assertEqual(_range_table(parse(text)), _range_table(parse(out)))

    def test_c27_explicit_combo_ordering(self) -> None:
        """C27: explicit combos sort ascending by (deck_index[lo],
        deck_index[hi]) of the canonical unordered pair, regardless of input
        order ('AhKd' (45,49) before 'AsKd' (45,51); see the module docstring
        deviation note — the designer literal assumed 's' precedes 'h' in
        SUITS, but SUITS = 'cdhs')."""
        out = format_range(parse("AsKd, AhKd"))
        self.assertEqual("AhKd 100%,AsKd 100%", out)
        self.assertEqual(out, format_range(parse("AhKd, AsKd")))

    def test_c28_non_uniform_class_falls_back_to_combos(self) -> None:
        """C28: from C14 ('AKs' + 'AsKs 30%') — no bare 'AKs' class token;
        the positive combos are emitted as explicit terms in deck-index
        order with their own percent suffixes; round-trips (C31 corpus
        carries this string)."""
        out = format_range(parse("AKs, AsKs 30%"))
        self.assertEqual("AcKc 100%,AdKd 100%,AhKh 100%,AsKs 30%", out)
        self.assertNotIn("AKs", out)
        self.assertEqual(
            _range_table(parse("AKs, AsKs 30%")), _range_table(parse(out))
        )

    def test_c29_non_representable_weights_fail_closed(self) -> None:
        """C29: a uniformly non-integer-percent class (all AKs at 1/3) and a
        mixed non-representable class (3 at 1, one at 1/3) both raise
        NotationError naming the class/combo and the exact Fraction weight —
        never a float approximation."""
        combos = _class_combos(("A", "K", "suited"))
        uniform = Range()
        for combo in combos:
            uniform = uniform.set_weight(combo, Fraction(1, 3))
        with self.assertRaises(NotationError) as manager:
            format_range(uniform)
        message = str(manager.exception)
        self.assertIn("AKs", message)
        self.assertIn("1/3", message)
        mixed = parse("AKs").set_weight(combos[0], Fraction(1, 3))
        with self.assertRaises(NotationError) as manager:
            format_range(mixed)
        message = str(manager.exception)
        self.assertIn("AcKc", message)
        self.assertIn("1/3", message)

    def test_c30_zero_range_rejected(self) -> None:
        """C30: format_range(Range()) raises NotationError with the
        'cannot format' phrase; a single explicitly zero-weight combo range
        stays rejected (total_mass() == 0)."""
        with self.assertRaises(NotationError) as manager:
            format_range(Range())
        self.assertIn("cannot format a zero range", str(manager.exception))
        zeroed = Range().set_weight(_combo("As", "Ks"), Fraction(0))
        with self.assertRaises(NotationError) as manager:
            format_range(zeroed)
        self.assertIn("cannot format a zero range", str(manager.exception))

    def test_c31_round_trip_corpus_bijection(self) -> None:
        """C31 (AC1): for every corpus string s — grammar branches, '+' and
        suffix forms, explicit/pair combos, mixed class+combo in both
        positional orders, and the canonical formatter outputs themselves —
        parse(format_range(parse(s))) has the full exact-Fraction 1,326
        table of parse(s) (zeros included), format_range is deterministic
        (two calls equal) and idempotent
        (format(parse(format(parse(s)))) == format(parse(s)))."""
        self.assertTrue(_CORPUS)
        for text in _CORPUS:
            with self.subTest(text=text):
                base = _range_table(parse(text))
                formatted = format_range(parse(text))
                self.assertEqual(base, _range_table(parse(formatted)))
                self.assertEqual(formatted, format_range(parse(text)))
                self.assertEqual(formatted, format_range(parse(formatted)))

    def test_c32_public_surface_and_oracle_sanity(self) -> None:
        """C32: the public surface is exactly {parse, format_range,
        NotationError}; NotationError subclasses ValueError; the in-test
        oracle sanity (169 classes over the 1,326-combo public
        enumeration)."""
        self.assertEqual(
            {"parse", "format_range", "NotationError"}, set(notation.__all__)
        )
        self.assertTrue(issubclass(notation.NotationError, ValueError))
        for name in ("parse", "format_range", "NotationError"):
            self.assertTrue(hasattr(notation, name), name)
        self.assertEqual(1326, len(_ORACLE_ENUMERATION))
        self.assertEqual(1326, len(_ORACLE_ALL_PAIRS))
        self.assertEqual(169, len(_ORACLE_CLASS_COMBOS))
        self.assertEqual(1326, sum(len(v) for v in _ORACLE_CLASS_COMBOS.values()))


if __name__ == "__main__":
    unittest.main()
