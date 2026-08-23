"""Money is integer cents. Floats never touch it.

A benchmark that reports a 1-cent discrepancy as a violation cannot afford
binary floating point anywhere in the money path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Money:
    """An amount in minor units (cents) of a single currency."""

    cents: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise TypeError(f"cents must be int, got {type(self.cents).__name__}")

    @classmethod
    def from_dollars(cls, dollars: str, currency: str = "USD") -> "Money":
        """Parse from a decimal string. Deliberately not from float.

        >>> Money.from_dollars("12.34")
        Money(cents=1234, currency='USD')
        """
        negative = dollars.startswith("-")
        if negative:
            dollars = dollars[1:]
        if "." in dollars:
            whole, _, frac = dollars.partition(".")
            if len(frac) > 2:
                raise ValueError(f"more precision than cents: {dollars!r}")
            frac = frac.ljust(2, "0")
        else:
            whole, frac = dollars, "00"
        cents = int(whole or "0") * 100 + int(frac)
        return cls(-cents if negative else cents, currency)

    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.cents + other.cents, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.cents - other.cents, self.currency)

    def __str__(self) -> str:
        sign = "-" if self.cents < 0 else ""
        whole, frac = divmod(abs(self.cents), 100)
        return f"{sign}{whole}.{frac:02d} {self.currency}"


ZERO = Money(0)
