"""Money conventions.

These assert the *rules* from profit-v1, not the current behaviour of the
module. Each test should fail if someone relaxes a convention for convenience,
which is the only reason any of them would ever be relaxed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.money import (
    ESTIMATE_PRECISION,
    SETTLED_PRECISION,
    Currency,
    CurrencyMismatch,
    InvalidMoney,
    Money,
    total,
)


class TestFloatsAreRefused:
    """profit-v1 prohibits binary floating point for monetary arithmetic."""

    def test_cannot_construct_from_float(self):
        with pytest.raises(InvalidMoney, match="float"):
            Money(12.34, "USD")

    def test_cannot_construct_from_float_even_when_exact(self):
        # 0.5 is exactly representable in binary. Still refused: the rule is
        # "no floats", not "no floats that happen to be lossy", because the
        # second version requires judgement at every call site.
        with pytest.raises(InvalidMoney):
            Money(0.5, "USD")

    def test_cannot_multiply_by_float(self):
        with pytest.raises(InvalidMoney):
            Money("10.00", "USD") * 1.5

    def test_string_construction_is_exact(self):
        assert Money("0.1", "USD") + Money("0.2", "USD") == Money("0.3", "USD")


class TestCurrencyIsInseparable:
    """BR-12: every amount carries its currency and currencies are never summed."""

    def test_adding_different_currencies_raises(self):
        with pytest.raises(CurrencyMismatch):
            Money("10.00", "USD") + Money("10.00", "EUR")

    def test_subtracting_different_currencies_raises(self):
        with pytest.raises(CurrencyMismatch):
            Money("10.00", "USD") - Money("10.00", "GBP")

    def test_comparing_different_currencies_raises(self):
        with pytest.raises(CurrencyMismatch):
            _ = Money("10.00", "USD") < Money("10.00", "EUR")

    def test_currency_must_be_iso_4217_shaped(self):
        for bad in ["usd", "US", "USDD", "", "1SD"]:
            with pytest.raises(InvalidMoney):
                Currency(bad)

    def test_there_is_no_default_currency(self):
        # Money requires a currency positionally. If this ever becomes optional,
        # an amount can lose its denomination and BR-12 is unenforceable.
        with pytest.raises(TypeError):
            Money("10.00")  # type: ignore[call-arg]


class TestUnknownIsNotZero:
    """profit-v1: a missing component withholds the figure, never defaults."""

    def test_zero_must_be_explicit(self):
        assert Money.zero("USD").amount == Decimal("0")

    def test_no_public_helper_converts_none_to_money(self):
        """Behavioural, not a spelling blocklist.

        An earlier version asserted that three specific identifiers were
        absent, which any differently-named helper walks straight past. This
        calls every public callable with None and requires each to refuse
        rather than quietly produce a zero.
        """
        import inspect

        import src.money as money_module

        for name in dir(money_module):
            if name.startswith("_"):
                continue
            obj = getattr(money_module, name)
            if not callable(obj) or inspect.isclass(obj):
                continue
            try:
                result = obj(None)
            except Exception:
                continue  # refusing is correct
            assert not isinstance(result, Money), (
                f"money.{name}(None) returned {result!r}. Unknown must never "
                f"become an amount -- it produces a confident wrong profit."
            )

    def test_total_rejects_none_entries(self):
        with pytest.raises(TypeError):
            total([Money("1.00", "USD"), None], "USD")  # type: ignore[list-item]

    def test_total_of_nothing_is_a_denominated_zero(self):
        assert total([], "USD") == Money.zero("USD")


class TestRoundingHappensOnce:
    """profit-v1: exact intermediates, one half-up rounding at the end."""

    def test_rounding_is_half_up_not_bankers(self):
        assert Money("0.125", "USD").as_settled() == Money("0.13", "USD")
        assert Money("0.135", "USD").as_settled() == Money("0.14", "USD")

    def test_estimates_keep_four_decimals(self):
        # 15% of 27.30 is exactly 4.095. Rounding it to 4.10 before summing
        # shifted two of twenty real orders by a cent, which is why estimates
        # are stored at 4dp.
        fee = Money("27.30", "USD") * Decimal("0.15")
        assert fee.as_estimate() == Money("4.0950", "USD")

    def test_rounding_intermediates_changes_the_answer(self):
        """The regression this precision split exists to prevent."""
        revenue = Money("27.30", "USD")
        components = [Money("7.99", "USD"), Money("4.095", "USD"),
                      Money("6.07", "USD"), Money("1.50", "USD")]

        once = (revenue - total(components, "USD")).as_settled()

        each_step = revenue
        for c in components:
            each_step = each_step - c.as_settled()
        each_step = each_step.as_settled()

        assert once == Money("7.65", "USD")
        assert each_step == Money("7.64", "USD")
        assert once != each_step

    def test_precision_constants_match_profit_v1(self):
        assert SETTLED_PRECISION == Decimal("0.01")
        assert ESTIMATE_PRECISION == Decimal("0.0001")


class TestRatioForRoi:
    """US-013 AC-03: a zero denominator is Unavailable, not zero or infinity."""

    def test_zero_denominator_returns_none(self):
        assert Money("10.00", "USD").ratio_to(Money.zero("USD")) is None

    def test_ratio_does_not_raise_on_zero(self):
        # An exception here would invite a bare except that swallows it and
        # yields zero, which is the outcome the rule forbids.
        Money("10.00", "USD").ratio_to(Money.zero("USD"))

    def test_roi_matches_the_golden_definition(self):
        # GR-01 from the real orders: profit -4.20 over total cost 79.08.
        roi = Money("-4.20", "USD").ratio_to(Money("79.08", "USD"))
        assert roi == Decimal("-0.0531")

    def test_ratio_across_currencies_raises(self):
        with pytest.raises(CurrencyMismatch):
            Money("1.00", "USD").ratio_to(Money("1.00", "EUR"))


class TestImmutability:
    def test_money_cannot_be_mutated(self):
        m = Money("1.00", "USD")
        with pytest.raises(AttributeError):
            m.amount = Decimal("999")  # type: ignore[misc]

    def test_currency_cannot_be_mutated(self):
        c = Currency("USD")
        with pytest.raises(AttributeError):
            c.code = "EUR"  # type: ignore[misc]
