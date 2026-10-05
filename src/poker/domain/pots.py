"""Deterministic pot accounting from normalized actions (POKER-CORE-POTS-001).

Contract (planner S1 + test-design oracle O1..O7):

* ``normalize_contributions`` folds a sequence of canonical :class:`Action`
  into per-player cumulative chip contributions, using sequence order and the
  per-street ``ADDED`` vs ``TOTAL_TO`` amount semantics.
* ``compute_uncalled_returns`` derives the chips that were never contested
  (above the highest contested all-in level) as ``UncalledReturn`` records.
* ``construct_side_pots`` builds one :class:`Pot` per contested level
  (ascending) from the per-level contribution slices, rake ``Chips(0)``.
* ``effective_stacks`` reports ``initial_stack - contribution`` per player.
* ``build_pots`` runs the fixed pipeline above plus the optional rake hook and
  is deterministic: identical input produces deeply identical output.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

from poker.domain.game import (  # single version source for the whole domain
    SCHEMA_VERSION,
)
from poker.domain.game import (
    Action,
    ActionKind,
    AmountSemantics,
    Street,
    Pot,
)
from poker.domain.money import Chips
from poker.math.chips import clamp as chips_clamp

__all__ = [
    "SCHEMA_VERSION",
    "InsufficientContextError",
    "PotBuildResult",
    "UncalledReturn",
    "build_pots",
    "compute_uncalled_returns",
    "construct_side_pots",
    "effective_stacks",
    "normalize_contributions",
]


class InsufficientContextError(ValueError):
    """Raised when the provided context cannot answer the question (fail closed)."""


@dataclasses.dataclass(frozen=True)
class UncalledReturn:
    """A (player, amount) record of chips returned because they were never contested."""

    player: str
    amount: Chips

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Chips):
            raise TypeError(
                f"UncalledReturn.amount must be Chips, got {type(self.amount).__name__}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "player": self.player,
            "amount": self.amount.to_dict(),
        }


_UNCALLED_KEYS = frozenset({"schema_version", "player", "amount"})

_RESULT_KEYS = frozenset(
    {"schema_version", "pots", "uncalled", "contributions", "rake", "effective_stacks"}
)


def _chips_mapping(payload: dict) -> dict[str, Chips]:
    out: dict[str, Chips] = {}
    for name, value in payload.items():
        if type(name) is not str or not name.strip():
            raise ValueError("player names must be non-empty strings")
        if not isinstance(value, Chips):
            raise TypeError(
                f"contribution for {name!r} must be Chips, got {type(value).__name__}"
            )
        out[name] = value
    return out


@dataclasses.dataclass(frozen=True)
class PotBuildResult:
    """Frozen result of one ``build_pots`` call."""

    pots: tuple
    uncalled: tuple
    contributions: dict
    rake: Chips
    effective_stacks: dict

    def __post_init__(self) -> None:
        pots = tuple(self.pots)
        uncalled = tuple(self.uncalled)
        if not all(isinstance(pot, Pot) for pot in pots):
            raise TypeError("PotBuildResult.pots must contain Pot instances")
        if not all(isinstance(row, UncalledReturn) for row in uncalled):
            raise TypeError("PotBuildResult.uncalled must contain UncalledReturn rows")
        if not isinstance(self.rake, Chips):
            raise TypeError(f"rake must be Chips, got {type(self.rake).__name__}")
        contributions = _chips_mapping(dict(self.contributions))
        effective_stacks = _chips_mapping(dict(self.effective_stacks))
        object.__setattr__(self, "pots", pots)
        object.__setattr__(self, "uncalled", uncalled)
        object.__setattr__(self, "contributions", MappingProxyType(contributions))
        object.__setattr__(self, "rake", self.rake)
        object.__setattr__(self, "effective_stacks", MappingProxyType(effective_stacks))

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "pots": [pot.to_dict() for pot in self.pots],
            "uncalled": [
                {
                    "schema_version": SCHEMA_VERSION,
                    "player": row.player,
                    "amount": row.amount.to_dict(),
                }
                for row in self.uncalled
            ],
            "contributions": {
                name: self.contributions[name].to_dict()
                for name in sorted(self.contributions)
            },
            "rake": self.rake.to_dict(),
            "effective_stacks": {
                name: self.effective_stacks[name].to_dict()
                for name in sorted(self.effective_stacks)
            },
        }

    @classmethod
    def from_dict(cls, payload: object) -> "PotBuildResult":
        from poker.domain import game

        game.require_schema_version(payload)
        game.require_exact_keys(payload, _RESULT_KEYS, "PotBuildResult")
        pots = tuple(Pot.from_dict(node) for node in payload["pots"])
        uncalled = tuple(
            _uncalled_from_dict(node) for node in payload["uncalled"]
        )
        contributions = {
            name: Chips.from_dict(node)
            for name, node in payload["contributions"].items()
        }
        effective_stacks = {
            name: Chips.from_dict(node)
            for name, node in payload["effective_stacks"].items()
        }
        return cls(
            pots=pots,
            uncalled=uncalled,
            contributions=contributions,
            rake=Chips.from_dict(payload["rake"]),
            effective_stacks=effective_stacks,
        )


def _uncalled_from_dict(payload: object) -> UncalledReturn:
    from poker.domain import game

    game.require_schema_version(payload)
    game.require_exact_keys(payload, _UNCALLED_KEYS, "UncalledReturn")
    return UncalledReturn(
        payload["player"], Chips.from_dict(payload["amount"])
    )


def normalize_contributions(actions):
    """Return per-player cumulative contributions (Mapping[str, Chips]).

    Actions are folded in sequence order (index tiebreak for equal sequence
    numbers). Per player and street: ``FOLD``/``CHECK`` add nothing;
    ``ADDED`` adds the full amount; ``TOTAL_TO`` adds
    ``amount - chips already in front on the current street`` (``amount <
    in_front`` is an impossible history -> ``ValueError``); ``POST`` counts
    as an ordinary contribution. Players with a zero total are omitted.
    """
    if actions is None or isinstance(actions, (str, bytes)) or not isinstance(
        actions, (list, tuple)
    ):
        raise TypeError(
            f"actions must be a list or tuple of Action, got {type(actions).__name__}"
        )
    totals: dict[str, int] = {}
    in_front: dict[str, int] = {}
    street_of: dict[str, Street] = {}
    for action in actions:
        if not isinstance(action, Action):
            raise TypeError(
                f"actions must contain Action instances, got {type(action).__name__}"
            )
    ordered = sorted(
        enumerate(actions), key=lambda pair: (pair[1].sequence, pair[0])
    )
    for _, action in ordered:
        actor = action.actor
        if action.kind in (ActionKind.FOLD, ActionKind.CHECK):
            continue
        if action.amount is None or action.amount_semantics is None:
            raise ValueError(
                f"action {action!r} must carry amount and amount semantics"
            )
        if street_of.get(actor) != action.street:
            in_front[actor] = 0
        street_of[actor] = action.street
        amount = action.amount.value
        if action.amount_semantics is AmountSemantics.TOTAL_TO:
            front = in_front.get(actor, 0)
            delta = amount - front
            if delta < 0:
                raise ValueError(
                    "impossible history: TOTAL_TO "
                    f"{amount} is below the {front} chips already in front "
                    f"for {actor!r} on {action.street.name}"
                )
        else:
            delta = amount
        in_front[actor] = in_front.get(actor, 0) + delta
        totals[actor] = totals.get(actor, 0) + delta
    return {
        name: Chips(value)
        for name, value in sorted(totals.items())
        if value > 0
    }


def _contested_levels(contributions):
    """Sorted distinct positive caps with >= 2 contributors at-or-above."""
    totals = [value.value for value in contributions.values()]
    caps = sorted({value for value in totals if value > 0})
    return tuple(
        cap for cap in caps if sum(1 for value in totals if value >= cap) >= 2
    )


def compute_uncalled_returns(contributions):
    """Return tuple[UncalledReturn, ...] for chips above contested levels.

    ``matched_p = min(total_p, t_k)`` for the highest contested cap ``t_k``
    (0 when no level is contested); the excess is returned exactly once.
    """
    if not isinstance(contributions, dict):
        raise TypeError(
            "contributions must be a mapping of player name to Chips"
        )
    levels = _contested_levels(contributions)
    top = levels[-1] if levels else 0
    returns = []
    for name in sorted(contributions):
        total = contributions[name].value
        excess = total - min(total, top)
        if excess > 0:
            returns.append(UncalledReturn(name, Chips(excess)))
    return tuple(returns)


def construct_side_pots(contributions):
    """Return tuple[Pot, ...] ascending by contested level, rake Chips(0).

    Level ``i`` holds every player's slice ``min(total, t_i) - t_{i-1}``;
    zero slices are pruned.
    """
    if not isinstance(contributions, dict):
        raise TypeError(
            "contributions must be a mapping of player name to Chips"
        )
    levels = _contested_levels(contributions)
    pots = []
    previous = 0
    for level in levels:
        slices = {}
        for name in sorted(contributions):
            total = contributions[name].value
            share = chips_clamp(
                min(total, level) - previous, 0, level - previous
            )
            if share.value > 0:
                slices[name] = share
        pots.append(Pot(slices, Chips(0)))
        previous = level
    return tuple(pots)


def effective_stacks(contributions, stacks):
    """Return per-player effective stacks (Mapping[str, Chips]).

    ``effective = initial_stack - cumulative contribution``; a contribution
    above the initial stack is an impossible history -> ``ValueError``;
    contributing players absent from ``stacks`` (or ``stacks=None`` with any
    nonzero contribution) -> ``InsufficientContextError``.
    """
    if not isinstance(contributions, dict):
        raise TypeError(
            "contributions must be a mapping of player name to Chips"
        )
    if stacks is None:
        if any(value.value > 0 for value in contributions.values()):
            raise InsufficientContextError(
                "initial stacks are required when any player contributes"
            )
        return {}
    if not isinstance(stacks, dict):
        raise TypeError(f"stacks must be a mapping, got {type(stacks).__name__}")
    effective: dict[str, Chips] = {}
    for name in sorted(contributions):
        total = contributions[name].value
        if name not in stacks:
            raise InsufficientContextError(
                f"no initial stack provided for contributing player {name!r}"
            )
        stack = stacks[name]
        if not isinstance(stack, Chips):
            raise TypeError(
                f"stacks[{name!r}] must be Chips, got {type(stack).__name__}"
            )
        if total > stack.value:
            raise ValueError(
                f"impossible history: contribution {total} exceeds initial "
                f"stack {stack.value} for {name!r}"
            )
        effective[name] = Chips(stack.value - total)
    return effective


def build_pots(actions, stacks=None, rake_policy=None):
    """Fixed pipeline: normalize -> uncalled -> side pots -> stacks -> rake.

    ``rake_policy`` (default ``None`` -> ``Chips(0)``) is invoked exactly once
    with the preliminary frozen ``PotBuildResult``; its return must be
    ``Chips`` else ``TypeError``. Deterministic: identical input yields
    deeply identical output.
    """
    if rake_policy is not None and not callable(rake_policy):
        raise TypeError(
            f"rake_policy must be callable or None, got {type(rake_policy).__name__}"
        )
    contributions = normalize_contributions(actions)
    uncalled = compute_uncalled_returns(contributions)
    pots = construct_side_pots(contributions)
    stacks_after = effective_stacks(contributions, stacks)
    preliminary = PotBuildResult(
        pots=pots,
        uncalled=uncalled,
        contributions=contributions,
        rake=Chips(0),
        effective_stacks=stacks_after,
    )
    if rake_policy is None:
        return preliminary
    rake = rake_policy(preliminary)
    if not isinstance(rake, Chips):
        raise TypeError(f"rake_policy must return Chips, got {type(rake).__name__}")
    return dataclasses.replace(preliminary, rake=rake)
