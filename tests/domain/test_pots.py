"""Binding oracle tests for deterministic pot accounting (POKER-CORE-POTS-001).

Implements the test-design oracle: golden ladder G1-G5 (O1/O4), conservation
algebra O2, replay identity O3, matched-amount identity O5, error oracle O6,
and the seeded property oracle O7 (seeds 1..20) with a mirror delta-ladder
reference implemented independently in this file.

Known oracle deviation (documented, not silent): the literal G5 pot list
(``Pot L100={UTG:100,BB:100,SB:50}=250``) is inconsistent with the oracle's
own pinned contested-level rule and with B3's contributor identity; see
``test_g5_oracle_single_pot_partition`` (skipped with full analysis) and
``docs/decisions/adr-0001-pots-contested-levels.md``.
"""

from __future__ import annotations

import json
import random
import unittest
from dataclasses import FrozenInstanceError

from poker.domain import game
from poker.domain.game import Action, ActionKind, AmountSemantics, Pot, Street
from poker.domain.money import Chips
from poker.domain.pots import (
    SCHEMA_VERSION,
    InsufficientContextError,
    PotBuildResult,
    UncalledReturn,
    build_pots,
    effective_stacks,
    normalize_contributions,
)
from poker.math import add, clamp, sum_all

_PROVENANCE = "hh-0001"


# ---------------------------------------------------------------------------
# Action builders (canonical game.Action instances)
# ---------------------------------------------------------------------------


def _act(actor, street, kind, amount=None, semantics=None, seq=0, prov=_PROVENANCE):
    return Action(actor, street, kind, amount, semantics, seq, prov)


def _added(actor, amount, seq, street=Street.PREFLOP, kind=ActionKind.BET):
    return _act(actor, street, kind, Chips(amount), AmountSemantics.ADDED, seq)


def _call_added(actor, amount, seq, street=Street.PREFLOP):
    return _act(actor, street, ActionKind.CALL, Chips(amount), AmountSemantics.ADDED, seq)


def _total_to(actor, total, seq, street=Street.PREFLOP):
    return _act(actor, street, ActionKind.RAISE, Chips(total), AmountSemantics.TOTAL_TO, seq)


def _post(actor, amount, seq, street=Street.PREFLOP):
    return _act(actor, street, ActionKind.POST, Chips(amount), AmountSemantics.ADDED, seq)


def _fold(actor, seq, street=Street.PREFLOP):
    return _act(actor, street, ActionKind.FOLD, None, None, seq)


def _check(actor, seq, street=Street.PREFLOP):
    return _act(actor, street, ActionKind.CHECK, None, None, seq)


# ---------------------------------------------------------------------------
# Shared invariant oracle (O2 conservation, O5 matched identity, B5)
# ---------------------------------------------------------------------------


def _assert_oracle_invariants(tc, result) -> None:
    """Universal algebra: holds for ANY input, rake excluded from conservation."""
    contrib_total = sum(c.value for c in result.contributions.values())
    pot_total = sum(
        v.value for pot in result.pots for v in pot.contributions.values()
    )
    uncalled_total = sum(u.amount.value for u in result.uncalled)
    tc.assertEqual(contrib_total, pot_total + uncalled_total)
    # B2/B5: exactly-once returns, one record per player, amount >= Chips(1)
    uncalled_by_player = {u.player: u.amount.value for u in result.uncalled}
    tc.assertEqual(len(uncalled_by_player), len(result.uncalled))
    for amount in uncalled_by_player.values():
        tc.assertGreaterEqual(amount, 1)
    # B8: per-pot identity pot.total == contributions + rake
    matched_by_player: dict[str, int] = {}
    for pot in result.pots:
        pot_contrib = sum(v.value for v in pot.contributions.values())
        tc.assertEqual(pot.total.value, pot_contrib + pot.rake.value)
        tc.assertEqual(pot.rake.value, 0)
        for name, value in pot.contributions.items():
            matched_by_player[name] = matched_by_player.get(name, 0) + value.value
    # O5: matched + uncalled == contribution, 0 <= uncalled <= contribution
    for name, total in result.contributions.items():
        matched = matched_by_player.get(name, 0)
        uncalled = uncalled_by_player.get(name, 0)
        tc.assertEqual(matched + uncalled, total.value)
        tc.assertGreaterEqual(uncalled, 0)
        tc.assertLessEqual(uncalled, total.value)


# ---------------------------------------------------------------------------
# Mirror reference for the property oracle (independent of production code)
# ---------------------------------------------------------------------------


def _expected_thresholds(totals: dict[str, int]) -> list[int]:
    """Sorted distinct caps with >= 2 contributing players at-or-above (B4/A5)."""
    positive = [value for value in totals.values() if value > 0]
    caps = sorted(set(positive))
    return [
        cap for cap in caps if sum(1 for v in positive if v >= cap) >= 2
    ]


def _expected_pots(totals: dict[str, int]) -> tuple[Pot, ...]:
    thresholds = _expected_thresholds(totals)
    pots = []
    previous = 0
    for level in thresholds:
        layer = {}
        for name in sorted(totals):
            share = min(totals[name], level) - previous
            if share > 0:
                layer[name] = Chips(share)
        pots.append(Pot(layer))
        previous = level
    return tuple(pots)


def _expected_uncalled(totals: dict[str, int]) -> tuple[UncalledReturn, ...]:
    thresholds = _expected_thresholds(totals)
    top = thresholds[-1] if thresholds else 0
    out = []
    for name in sorted(totals):
        excess = totals[name] - min(totals[name], top)
        if excess > 0:
            out.append(UncalledReturn(name, Chips(excess)))
    return tuple(out)


def _g5_actions() -> list[Action]:
    return [
        _post("SB", 50, 0),
        _post("BB", 100, 1),
        _total_to("UTG", 300, 2),
        _act("BB", Street.PREFLOP, ActionKind.CALL, Chips(100), AmountSemantics.TOTAL_TO, 3),
        _fold("SB", 4),
    ]


# ---------------------------------------------------------------------------
# O1/O4 golden ladder
# ---------------------------------------------------------------------------


class GoldenLadderTests(unittest.TestCase):
    def test_g1_three_way_unequal_allins(self):
        actions = [
            _call_added("A", 50, 0),
            _call_added("B", 200, 1),
            _call_added("C", 500, 2),
        ]
        result = build_pots(
            actions,
            {"A": Chips(1000), "B": Chips(1000), "C": Chips(1000)},
        )
        self.assertEqual(
            result.pots,
            (
                Pot({"A": Chips(50), "B": Chips(50), "C": Chips(50)}),
                Pot({"B": Chips(150), "C": Chips(150)}),
            ),
        )
        self.assertEqual(result.uncalled, (UncalledReturn("C", Chips(300)),))
        self.assertEqual(
            result.contributions,
            {"A": Chips(50), "B": Chips(200), "C": Chips(500)},
        )
        self.assertEqual(result.rake, Chips(0))
        _assert_oracle_invariants(self, result)

    def test_g2_heads_up_uncalled_bet(self):
        actions = [_added("A", 100, 0), _fold("B", 1)]
        result = build_pots(actions, {"A": Chips(100), "B": Chips(0)})
        self.assertEqual(result.pots, ())
        self.assertEqual(result.uncalled, (UncalledReturn("A", Chips(100)),))
        self.assertEqual(result.contributions, {"A": Chips(100)})
        _assert_oracle_invariants(self, result)

    def test_g3_four_way_two_equal_allins(self):
        actions = [
            _call_added("A", 100, 0),
            _call_added("B", 100, 1),
            _call_added("C", 300, 2),
            _call_added("D", 300, 3),
        ]
        result = build_pots(
            actions,
            {name: Chips(1000) for name in ("A", "B", "C", "D")},
        )
        self.assertEqual(
            result.pots,
            (
                Pot(
                    {
                        "A": Chips(100),
                        "B": Chips(100),
                        "C": Chips(100),
                        "D": Chips(100),
                    }
                ),
                Pot({"C": Chips(200), "D": Chips(200)}),
            ),
        )
        self.assertEqual(result.uncalled, ())
        self.assertEqual(result.rake, Chips(0))
        _assert_oracle_invariants(self, result)

    def test_g4_allin_below_sb(self):
        actions = [
            _post("SB", 50, 0),
            _post("BB", 100, 1),
            _call_added("C", 30, 2),
        ]
        result = build_pots(
            actions,
            {"SB": Chips(1000), "BB": Chips(1000), "C": Chips(30)},
        )
        self.assertEqual(
            result.pots,
            (
                Pot({"C": Chips(30), "SB": Chips(30), "BB": Chips(30)}),
                Pot({"SB": Chips(20), "BB": Chips(20)}),
            ),
        )
        self.assertEqual(result.uncalled, (UncalledReturn("BB", Chips(50)),))
        self.assertEqual(
            result.contributions,
            {"BB": Chips(100), "C": Chips(30), "SB": Chips(50)},
        )
        _assert_oracle_invariants(self, result)

    def test_g5_chip_identity(self):
        result = build_pots(
            _g5_actions(),
            {"SB": Chips(1000), "BB": Chips(1000), "UTG": Chips(1000)},
        )
        self.assertEqual(
            result.contributions,
            {"BB": Chips(100), "SB": Chips(50), "UTG": Chips(300)},
        )
        self.assertEqual(result.uncalled, (UncalledReturn("UTG", Chips(200)),))
        matched: dict[str, int] = {}
        for pot in result.pots:
            for name, value in pot.contributions.items():
                matched[name] = matched.get(name, 0) + value.value
        self.assertEqual(matched, {"SB": 50, "BB": 100, "UTG": 100})
        self.assertEqual(sum(matched.values()), 250)
        for value in matched.values():
            self.assertLessEqual(value, 100)  # no level-300 pot
        _assert_oracle_invariants(self, result)

    def test_g5_street_reset_continuation(self):
        actions = _g5_actions() + [
            _added("UTG", 100, 5, street=Street.FLOP),
            _total_to("UTG", 250, 6, street=Street.TURN),
        ]
        result = build_pots(
            actions,
            {"SB": Chips(1000), "BB": Chips(1000), "UTG": Chips(1000)},
        )
        # flop bet adds exactly 100 with nothing in front; turn TOTAL_TO 250
        # adds exactly 250 (per-street in-front reset).
        self.assertEqual(
            result.contributions,
            {"BB": Chips(100), "SB": Chips(50), "UTG": Chips(650)},
        )
        self.assertEqual(result.uncalled, (UncalledReturn("UTG", Chips(550)),))
        _assert_oracle_invariants(self, result)

    @unittest.skip(
        "BLOCKED oracle finding (test-designer G5 literal "
        "'Pot L100={UTG:100,BB:100,SB:50}=250'): the literal single merged pot "
        "contradicts the oracle's own pinned contested-level rule (B4/A5 + "
        "implementation sketch: one pot per distinct cap with >=2 players "
        "at-or-above), which predicts L50={UTG:50,BB:50,SB:50} + "
        "L100={UTG:50,BB:50}; and contradicts B3's contributor identity: SB's "
        "matched contribution is min(50, top contested cap 100) = 50 < 100, so "
        "SB can never be an L100 contributor. G4 is structurally isomorphic to "
        "G5 (30/50/100 vs 50/100/300) and expects the SPLIT ladder, so no "
        "contribution-driven rule can satisfy both literals. Real poker "
        "likewise keeps SB's dead 50 in the main pot with a separate 50-100 "
        "side pot. All other G5 expectations (uncalled 200, conservation 450, "
        "per-player in-pot totals 100/100/50, no level-300 pot, street reset) "
        "are asserted in test_g5_chip_identity / test_g5_street_reset_"
        "continuation. Recorded in docs/decisions/adr-0001-pots-contested-"
        "levels.md; not silently weakened."
    )
    def test_g5_oracle_single_pot_partition(self):
        result = build_pots(
            _g5_actions(),
            {"SB": Chips(1000), "BB": Chips(1000), "UTG": Chips(1000)},
        )
        self.assertEqual(
            result.pots,
            (Pot({"UTG": Chips(100), "BB": Chips(100), "SB": Chips(50)}),),
        )


# ---------------------------------------------------------------------------
# Normalization semantics (B6) and fail-closed inputs
# ---------------------------------------------------------------------------


class NormalizationTests(unittest.TestCase):
    def test_normalize_semantics_and_ordering(self):
        # reversed input: normalization sorts by sequence (B9/O3)
        self.assertEqual(
            normalize_contributions(list(reversed(_g5_actions()))),
            {"BB": Chips(100), "SB": Chips(50), "UTG": Chips(300)},
        )

    def test_total_to_below_in_front_valueerror(self):
        actions = [_call_added("A", 100, 0), _total_to("A", 50, 1)]
        with self.assertRaises(ValueError) as ctx:
            normalize_contributions(actions)
        self.assertNotIsInstance(ctx.exception, InsufficientContextError)

    def test_non_action_input_typeerror(self):
        with self.assertRaises(TypeError):
            normalize_contributions(["not-an-action"])
        with self.assertRaises(TypeError):
            normalize_contributions(None)


# ---------------------------------------------------------------------------
# N1-N9 negatives
# ---------------------------------------------------------------------------


class NegativeTests(unittest.TestCase):
    def test_n1_empty_actions(self):
        result = build_pots([])
        self.assertEqual(result.pots, ())
        self.assertEqual(result.uncalled, ())
        self.assertEqual(result.contributions, {})
        self.assertEqual(result.rake, Chips(0))
        self.assertEqual(result.effective_stacks, {})
        self.assertEqual(
            json.dumps(build_pots([]).to_dict(), sort_keys=True),
            json.dumps(result.to_dict(), sort_keys=True),
        )

    def test_n2_single_allin_others_fold(self):
        actions = [_call_added("A", 200, 0), _fold("B", 1), _fold("C", 2)]
        result = build_pots(actions, {"A": Chips(200), "B": Chips(0), "C": Chips(0)})
        self.assertEqual(result.pots, ())
        self.assertEqual(result.uncalled, (UncalledReturn("A", Chips(200)),))
        _assert_oracle_invariants(self, result)

    def test_n3_two_equal_allins(self):
        actions = [_call_added("A", 500, 0), _call_added("B", 500, 1)]
        result = build_pots(actions, {"A": Chips(500), "B": Chips(500)})
        self.assertEqual(result.pots, (Pot({"A": Chips(500), "B": Chips(500)}),))
        self.assertEqual(result.uncalled, ())
        self.assertEqual(result.rake, Chips(0))
        _assert_oracle_invariants(self, result)

    def test_n4_effective_stack_overbet(self):
        actions = [_call_added("A", 150, 0), _fold("B", 1)]
        stacks = {"A": Chips(100), "B": Chips(50)}
        with self.assertRaises(ValueError) as ctx:
            build_pots(actions, stacks)
        self.assertNotIsInstance(ctx.exception, InsufficientContextError)
        with self.assertRaises(ValueError) as ctx:
            effective_stacks({"A": Chips(150)}, {"A": Chips(100)})
        self.assertNotIsInstance(ctx.exception, InsufficientContextError)
        # boundary: contribution exactly equal to the stack is legal
        legal = build_pots(
            [_call_added("A", 100, 0), _fold("B", 1)], stacks
        )
        self.assertEqual(legal.effective_stacks, {"A": Chips(0)})

    def test_n5_missing_stacks(self):
        actions = [_call_added("A", 150, 0), _call_added("B", 200, 1)]
        with self.assertRaises(InsufficientContextError):
            build_pots(actions, stacks=None)
        with self.assertRaises(InsufficientContextError):
            effective_stacks({"A": Chips(150)}, None)
        # stacks=None with all-zero contributions does not raise
        self.assertEqual(effective_stacks({}, None), {})
        self.assertEqual(effective_stacks({"Z": Chips(0)}, None), {})
        # contributing player absent from the stacks mapping
        with self.assertRaises(InsufficientContextError):
            build_pots(actions, {"A": Chips(150)})
        # fold-only hand never needs stacks
        self.assertEqual(
            build_pots([_fold("A", 0)], stacks=None).effective_stacks, {}
        )

    def test_n6_unknown_actor_fail_closed(self):
        actions = [_call_added("A", 100, 0), _call_added("GHOST", 100, 1)]
        stacks = {"A": Chips(1000), "B": Chips(1000)}
        with self.assertRaises(InsufficientContextError):
            build_pots(actions, stacks)

    def test_n7_rake_hook(self):
        actions = [
            _call_added("A", 100, 0),
            _call_added("B", 100, 1),
            _call_added("C", 300, 2),
        ]
        stacks = {name: Chips(1000) for name in ("A", "B", "C")}
        baseline = build_pots(actions, stacks)
        self.assertEqual(baseline.rake, Chips(0))
        for pot in baseline.pots:
            self.assertEqual(pot.rake, Chips(0))

        calls: list = []

        def policy(candidate):
            calls.append(candidate)
            return Chips(25)

        hooked = build_pots(actions, stacks, policy)
        self.assertEqual(len(calls), 1)  # A7: invoked exactly once
        self.assertIsInstance(calls[0], PotBuildResult)
        self.assertEqual(hooked.rake, Chips(25))
        self.assertEqual(hooked.pots, baseline.pots)
        for pot in hooked.pots:
            self.assertEqual(pot.rake, Chips(0))
            self.assertEqual(
                pot.total.value,
                sum(v.value for v in pot.contributions.values()) + pot.rake.value,
            )

        for bad in (25, 2.5, None, "25"):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    build_pots(actions, stacks, lambda candidate: bad)

    def test_n8_chips_helpers(self):
        self.assertEqual(clamp(Chips(5), Chips(0), Chips(3)), Chips(3))
        self.assertEqual(clamp(Chips(5), Chips(7), Chips(9)), Chips(7))
        self.assertEqual(clamp(Chips(5), Chips(0), Chips(9)), Chips(5))
        self.assertEqual(clamp(-2, -10, 20), Chips(0))  # negative -> Chips(0)
        self.assertEqual(clamp(Chips(0), Chips(0), Chips(0)), Chips(0))
        self.assertEqual(add(Chips(2), Chips(3)), Chips(5))
        self.assertEqual(add(Chips(0), Chips(0)), Chips(0))
        self.assertEqual(sum_all([]), Chips(0))
        self.assertEqual(sum_all([Chips(2), Chips(3), Chips(4)]), Chips(9))
        with self.assertRaises(ValueError):
            Chips(-1)
        with self.assertRaises(TypeError):
            Chips(1.5)
        with self.assertRaises(TypeError):
            Chips(True)

    def test_n9_zero_contribution_pruning(self):
        actions = [
            _check("C", 0),
            _fold("C", 1),
            _call_added("A", 100, 2),
            _fold("B", 3),
        ]
        result = build_pots(actions, {"A": Chips(100), "B": Chips(0), "C": Chips(0)})
        self.assertEqual(result.contributions, {"A": Chips(100)})
        self.assertEqual(result.pots, ())
        self.assertEqual(result.uncalled, (UncalledReturn("A", Chips(100)),))
        for pot in result.pots:
            self.assertNotIn("B", pot.contributions)
            self.assertNotIn("C", pot.contributions)


# ---------------------------------------------------------------------------
# Structural contract (schema, determinism, freezing)
# ---------------------------------------------------------------------------


class ContractTests(unittest.TestCase):
    @staticmethod
    def _hand_built_result() -> PotBuildResult:
        return PotBuildResult(
            pots=(Pot({"A": Chips(50)}),),
            uncalled=(UncalledReturn("B", Chips(7)),),
            contributions={"A": Chips(50), "B": Chips(7)},
            rake=Chips(0),
            effective_stacks={"A": Chips(50), "B": Chips(0)},
        )

    def test_schema_version_reexport(self):
        self.assertEqual(SCHEMA_VERSION, game.SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 1)

    def test_serialization_roundtrip(self):
        result = build_pots(
            _g5_actions(),
            {"SB": Chips(500), "BB": Chips(1000), "UTG": Chips(2000)},
        )
        payload = result.to_dict()
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "pots",
                "uncalled",
                "contributions",
                "rake",
                "effective_stacks",
            },
        )
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        stable = json.dumps(payload, sort_keys=True)
        self.assertEqual(stable, json.dumps(result.to_dict(), sort_keys=True))
        rebuilt = PotBuildResult.from_dict(payload)
        self.assertEqual(rebuilt, result)
        self.assertEqual(json.dumps(rebuilt.to_dict(), sort_keys=True), stable)

    def test_hand_built_roundtrip(self):
        result = self._hand_built_result()
        rebuilt = PotBuildResult.from_dict(result.to_dict())
        self.assertEqual(rebuilt, result)
        self.assertEqual(json.dumps(rebuilt.to_dict(), sort_keys=True),
                         json.dumps(result.to_dict(), sort_keys=True))

    def test_frozen_shapes(self):
        result = self._hand_built_result()
        with self.assertRaises(FrozenInstanceError):
            result.rake = Chips(5)
        with self.assertRaises(FrozenInstanceError):
            result.uncalled[0].amount = Chips(0)
        with self.assertRaises(TypeError):
            result.contributions["X"] = Chips(1)
        with self.assertRaises(TypeError):
            UncalledReturn("A", 100)

    def test_insufficient_context_is_valueerror(self):
        self.assertTrue(issubclass(InsufficientContextError, ValueError))


# ---------------------------------------------------------------------------
# O7 property oracle (seeds 1..20)
# ---------------------------------------------------------------------------


def _street_split(rng, total, streets):
    parts = [0] * streets
    remaining = total
    for index in range(streets - 1, 0, -1):
        if remaining and rng.random() < 0.6:
            take = rng.randint(0, remaining)
            parts[index] = take
            remaining -= take
    parts[0] = remaining
    return parts


def _generate_scenario(seed):
    rng = random.Random(seed)
    count = rng.randint(2, 5)
    players = [f"P{index}" for index in range(count)]
    stacks = {p: Chips(rng.randint(1, 1000)) for p in players}
    stacks_value = {p: stacks[p].value for p in players}
    streets = rng.randint(1, 4)
    totals = {
        p: (0 if rng.random() < 0.2 else rng.randint(1, stacks_value[p]))
        for p in players
    }
    parts = {p: _street_split(rng, totals[p], streets) for p in players}
    actions = []
    seq = 0
    for street_index in range(streets):
        street = Street(street_index)
        for p in players:
            share = parts[p][street_index]
            if share == 0:
                action = (
                    _fold(p, seq, street)
                    if rng.random() < 0.5
                    else _check(p, seq, street)
                )
                actions.append(action)
                seq += 1
                continue
            # Optional blind-style POST prefix on street 0 (counts toward the
            # same street share; the remainder completes the street-local
            # total via ADDED or TOTAL_TO).
            posted = 0
            if street_index == 0 and share > 1 and rng.random() < 0.3:
                posted = rng.randint(1, share - 1)
                actions.append(_post(p, posted, seq, street))
                seq += 1
                share -= posted
            in_front_current_street = posted
            if rng.random() < 0.5:
                kind = ActionKind.BET if in_front_current_street == 0 else ActionKind.CALL
                action = _act(
                    p, street, kind, Chips(share), AmountSemantics.ADDED, seq
                )
            else:
                kind = ActionKind.BET if in_front_current_street == 0 else ActionKind.RAISE
                action = _act(
                    p,
                    street,
                    kind,
                    Chips(share + in_front_current_street),
                    AmountSemantics.TOTAL_TO,
                    seq,
                )
            actions.append(action)
            seq += 1
    assert len({a.sequence for a in actions}) == len(actions)
    return actions, stacks, {p: c for p, c in totals.items() if c > 0}


class PropertyOracleTests(unittest.TestCase):
    def test_p1_conservation(self):
        for seed in range(1, 21):
            with self.subTest(seed=seed):
                actions, stacks, totals = _generate_scenario(seed)
                result = build_pots(actions, stacks)
                # normalization oracle: intended per-player totals decoded
                self.assertEqual(
                    result.contributions,
                    {p: Chips(v) for p, v in sorted(totals.items())},
                )
                _assert_oracle_invariants(self, result)

    def test_p2_levels(self):
        for seed in range(1, 21):
            with self.subTest(seed=seed):
                actions, stacks, _ = _generate_scenario(seed)
                result = build_pots(actions, stacks)
                totals = {
                    name: chips.value
                    for name, chips in result.contributions.items()
                }
                thresholds = _expected_thresholds(totals)
                self.assertEqual(result.pots, _expected_pots(totals))
                self.assertEqual(result.uncalled, _expected_uncalled(totals))
                top = thresholds[-1] if thresholds else 0
                matched = {name: min(v, top) for name, v in totals.items()}
                for index, level in enumerate(thresholds):
                    contributors = {
                        name for name, value in totals.items()
                        if matched[name] >= level
                    }
                    self.assertEqual(
                        set(result.pots[index].contributions), contributors
                    )

    def test_p3_determinism_and_stacks(self):
        for seed in range(1, 21):
            with self.subTest(seed=seed):
                actions, stacks, _ = _generate_scenario(seed)
                first = build_pots(actions, stacks)
                second = build_pots(actions, stacks)
                self.assertEqual(first, second)
                shuffled = list(actions)
                random.Random(seed).shuffle(shuffled)
                third = build_pots(shuffled, stacks)
                self.assertEqual(first, third)
                self.assertEqual(
                    json.dumps(first.to_dict(), sort_keys=True),
                    json.dumps(third.to_dict(), sort_keys=True),
                )
                for name, eff in first.effective_stacks.items():
                    self.assertGreaterEqual(eff.value, 0)
                    self.assertEqual(
                        eff.value,
                        stacks[name].value - first.contributions[name].value,
                    )
                contributing = set(first.contributions)
                self.assertEqual(
                    sum(e.value for e in first.effective_stacks.values())
                    + sum(c.value for c in first.contributions.values()),
                    sum(stacks[p].value for p in contributing),
                )


if __name__ == "__main__":
    unittest.main()
