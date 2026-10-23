"""Monetary arithmetic.

Implements the precision half of ``profit-v1``. Every financial figure in the
application passes through here, so the conventions are enforced structurally
rather than documented and hoped for.

Three rules, each of which exists because violating it is easier:

1.  **Binary floating point is never used.** ``0.1 + 0.2`` is not ``0.3`` and a
    cent lost at the sixth cost component never comes back. ``Money`` refuses
    to be constructed from a ``float`` at all.

2.  **An amount is inseparable from its currency.** BR-12 stores a currency
    beside every monetary column, and the only way to guarantee that is to make
    a bare amount unrepresentable. Adding two currencies raises.

3.  **Unknown is not zero.** A missing cost is ``None``, never ``Money.zero()``.
    This module deliberately offers no "default to zero" helper, because the
    moment one exists it will be used in a sum and produce a confident wrong
    profit instead of an honest incomplete one.

Precision follows ``profit-v1``: estimates at 4 dp because they are derived and
precision is free, everything else at 2 dp because it is money that moved.
Intermediate arithmetic stays exact; rounding happens once, at the end.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Final

__all__ = [
    "Money",
    "Currency",
    "CurrencyMismatch",
    "InvalidMoney",
    "SETTLED_PRECISION",
    "ESTIMATE_PRECISION",
    "RATIO_PRECISION",
]

# profit-v1 precision. Named rather than inlined so a reader of a call site can
# see which rule is being applied.
SETTLED_PRECISION: Final = Decimal("0.01")
ESTIMATE_PRECISION: Final = Decimal("0.0001")
RATIO_PRECISION: Final = Decimal("0.0001")

_ISO_4217 = re.compile(r"^[A-Z]{3}$")


class InvalidMoney(ValueError):
    """An amount could not be represented exactly."""


class CurrencyMismatch(ValueError):
    """An operation would combine two currencies.

    BR-12: currencies are never summed. There is no conversion in this release,
    so this is an error rather than a prompt to convert.
    """


class Currency:
    """An ISO-4217 code.

    A type rather than a bare string so that a currency cannot be forgotten,
    mistyped, or quietly defaulted. There is deliberately no default value and
    no ``Currency.USD`` constant -- a currency always comes from the store that
    owns the amount.
    """

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not _ISO_4217.match(code):
            raise InvalidMoney(
                f"currency must be a three-letter uppercase ISO-4217 code, got {code!r}"
            )
        object.__setattr__(self, "code", code)

    def __setattr__(self, *_: object) -> None:
        raise AttributeError("Currency is immutable")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Currency) and other.code == self.code

    def __hash__(self) -> int:
        return hash(("Currency", self.code))

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"Currency({self.code!r})"


class Money:
    """An exact decimal amount bound to a currency.

    Construct from ``str``, ``int`` or ``Decimal``. Constructing from ``float``
    raises: by the time a value is a float the damage is already done, and
    accepting it "just for convenience" is how float arithmetic reaches a
    financial system.
    """

    __slots__ = ("amount", "currency")

    def __init__(self, amount: str | int | Decimal, currency: Currency | str) -> None:
        if isinstance(amount, float):
            raise InvalidMoney(
                "Money cannot be built from a float. Pass a string such as "
                "Money('12.34', 'USD') so the value is exact. profit-v1 "
                "prohibits binary floating point for monetary arithmetic."
            )
        if isinstance(amount, bool):
            raise InvalidMoney("Money cannot be built from a bool")
        try:
            value = Decimal(amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise InvalidMoney(f"not an exact decimal amount: {amount!r}") from exc
        if not value.is_finite():
            raise InvalidMoney(f"amount must be finite, got {amount!r}")

        object.__setattr__(self, "amount", value)
        object.__setattr__(
            self, "currency", currency if isinstance(currency, Currency) else Currency(currency)
        )

    def __setattr__(self, *_: object) -> None:
        raise AttributeError("Money is immutable")

    # -- construction -------------------------------------------------------

    @classmethod
    def zero(cls, currency: Currency | str) -> "Money":
        """An explicit, known zero.

        Distinct from ``None``, which means unknown. US-008 AC-03 requires the
        two to stay distinguishable, so this is named and deliberate rather
        than something a missing value decays into.
        """
        return cls(Decimal("0"), currency)

    # -- arithmetic ---------------------------------------------------------

    def _check(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} with {other.currency}. "
                "BR-12: currencies are never summed and no conversion exists."
            )

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: int | Decimal) -> "Money":
        if isinstance(factor, float):
            raise InvalidMoney("cannot multiply Money by a float")
        return Money(self.amount * Decimal(factor), self.currency)

    __rmul__ = __mul__

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def ratio_to(self, other: "Money") -> Decimal | None:
        """Divide by another amount, for ROI and margin.

        Returns ``None`` when the denominator is zero. US-013 AC-03 requires a
        zero-denominator ROI to read as Unavailable -- never zero, never
        infinity, never an exception that a caller might swallow.
        """
        self._check(other)
        if other.amount == 0:
            return None
        return (self.amount / other.amount).quantize(
            RATIO_PRECISION, rounding=ROUND_HALF_UP
        )

    # -- rounding -----------------------------------------------------------

    def quantize(self, precision: Decimal) -> "Money":
        """Round half-up to the given precision.

        Call once, on a final result. profit-v1 forbids rounding intermediate
        values: doing so shifts roughly one order in ten by a cent, which was
        measured against twenty real orders rather than assumed.
        """
        return Money(
            self.amount.quantize(precision, rounding=ROUND_HALF_UP), self.currency
        )

    def as_settled(self) -> "Money":
        """Round to 2 dp: money that actually moved."""
        return self.quantize(SETTLED_PRECISION)

    def as_estimate(self) -> "Money":
        """Round to 4 dp: a derived figure, where precision is free."""
        return self.quantize(ESTIMATE_PRECISION)

    # -- comparison ---------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.currency == other.currency and self.amount == other.amount

    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount >= other.amount

    def __hash__(self) -> int:
        return hash(("Money", self.amount, self.currency))

    # -- representation -----------------------------------------------------

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

    def __repr__(self) -> str:
        return f"Money({str(self.amount)!r}, {self.currency.code!r})"


def total(amounts: list[Money], currency: Currency | str) -> Money:
    """Sum amounts that are all known.

    Takes an explicit currency so that summing an empty list still produces a
    correctly denominated zero rather than guessing or returning ``None``.

    This deliberately does **not** accept ``None`` entries. A caller holding an
    unknown component must decide what that means -- normally that the result
    is incomplete and no figure may be produced -- rather than having this
    function silently treat it as zero.
    """
    result = Money.zero(currency)
    for item in amounts:
        result = result + item
    return result
