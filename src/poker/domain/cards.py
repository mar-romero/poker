"""Canonical card identity for the standard 52-card deck.

Card identity is a ``(rank, suit)`` pair with strict canonical symbols:
ranks ``23456789TJQKA`` (uppercase; ten is ``T``, never ``10``) and suits
``cdhs`` (lowercase). ``DECK`` is the deterministic, fully ordered and unique
52-card deck.
"""

from __future__ import annotations

import dataclasses

__all__ = ["RANKS", "SUITS", "DECK", "Card", "assert_unique_cards"]

RANKS: tuple[str, ...] = tuple("23456789TJQKA")
SUITS: tuple[str, ...] = tuple("cdhs")

_CARD_KEYS = frozenset({"schema_version", "rank", "suit"})


@dataclasses.dataclass(frozen=True)
class Card:
    """One card of the standard 52-card deck; identity is ``(rank, suit)``."""

    rank: str
    suit: str

    def __post_init__(self) -> None:
        if type(self.rank) is not str:
            raise TypeError(
                f"Card.rank must be a str, got {type(self.rank).__name__}"
            )
        if type(self.suit) is not str:
            raise TypeError(
                f"Card.suit must be a str, got {type(self.suit).__name__}"
            )
        if self.rank not in RANKS:
            raise ValueError(
                f"Card.rank must be one of {''.join(RANKS)!r} (uppercase, ten is 'T'), got {self.rank!r}"
            )
        if self.suit not in SUITS:
            raise ValueError(
                f"Card.suit must be one of {''.join(SUITS)!r} (lowercase), got {self.suit!r}"
            )

    @property
    def symbol(self) -> str:
        """Canonical two-character symbol, e.g. ``As`` or ``Tc``."""
        return f"{self.rank}{self.suit}"

    def __str__(self) -> str:
        return self.symbol

    def to_dict(self) -> dict:
        from poker.domain import game  # single source of SCHEMA_VERSION

        return {
            "schema_version": game.SCHEMA_VERSION,
            "rank": self.rank,
            "suit": self.suit,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Card":
        from poker.domain import game

        game.require_schema_version(payload)
        game.require_exact_keys(payload, _CARD_KEYS, "Card")
        return cls(payload["rank"], payload["suit"])


DECK: tuple[Card, ...] = tuple(
    Card(rank, suit) for rank in RANKS for suit in SUITS
)


def assert_unique_cards(cards: object) -> None:
    """Raise ``ValueError`` when *cards* contains any duplicate card."""
    seen: set[str] = set()
    for card in cards:
        if not isinstance(card, Card):
            raise TypeError(f"expected Card instances, got {type(card).__name__}")
        if card.symbol in seen:
            raise ValueError(f"duplicate card: {card.symbol!r}")
        seen.add(card.symbol)
