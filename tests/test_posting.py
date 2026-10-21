"""Tests for the pure posting logic.

These encode the money rules from DATA_MODEL.md. They need no database and no
Walmart credentials -- which is the whole justification for splitting posting
away from ingestion.
"""

import datetime as dt

import pytest

from posting import CostBasis, landed_total, post, recognition_date


# ---------------------------------------------------------------- fixtures

def order(po="PO1", customer="C1", date="2026-07-01T00:00:00+00:00"):
    return {"purchase_order_id": po, "customer_order_id": customer, "order_date": date}


def line(po="PO1", n="1", sku="SKU1", qty=1, revenue=100.0, shipping=0.0,
         status="Shipped", shipped_at=None):
    return {
        "purchase_order_id": po, "line_number": n, "sku": sku, "quantity": qty,
        "product_revenue": revenue, "shipping_revenue": shipping,
        "status": status, "shipped_at": shipped_at,
    }


def purchase(sku="SKU1", qty=10, unit=5.0, tax=0.0, ship=0.0,
             date="2026-06-01", pid="p1"):
    return {
        "id": pid, "sku": sku, "quantity": qty, "unit_cost": unit,
        "sales_tax": tax, "shipping": ship, "purchase_date": date,
        "created_at": "2026-06-01T00:00:00+00:00",
    }


def run(**kw):
    kw.setdefault("store_id", "S")
    kw.setdefault("orders", [order()])
    kw.setdefault("order_lines", [line()])
    kw.setdefault("purchases", [])
    kw.setdefault("product_costs", [])
    return post(**kw)


def amounts(result, entry_type):
    return [e["amount"] for e in result.entries if e["entry_type"] == entry_type]


# ------------------------------------------------------------ cost basis

def test_landed_total_matches_legacy_definition():
    # (qty * unit_cost) + sales_tax + shipping -- schema.sql:257
    assert landed_total(purchase(qty=2, unit=15.99, tax=1.90, ship=-3.20)) == pytest.approx(30.68)


def test_moving_average_blends_purchases():
    b = CostBasis()
    b.buy(10, 50.0)                    # 10 @ 5.00
    assert b.unit_cost == pytest.approx(5.0)
    b.buy(10, 80.0)                    # 10 @ 8.00
    assert b.unit_cost == pytest.approx(6.5)
    cogs, reason = b.sell(5)
    assert cogs == pytest.approx(32.5)
    assert reason is None
    assert b.qty == pytest.approx(15)


def test_sell_without_any_purchase_has_no_basis():
    cogs, reason = CostBasis().sell(1)
    assert cogs is None
    assert reason == "no_purchase_history"


def test_overselling_is_costed_but_flagged():
    b = CostBasis()
    b.buy(2, 10.0)
    cogs, reason = b.sell(5)
    assert cogs == pytest.approx(25.0)
    assert reason == "insufficient_qty"


# -------------------------------------------------- the headline guarantee

def test_a_later_purchase_does_not_change_an_earlier_sale():
    """The bug this whole rebuild exists to fix.

    sku_landed_cost (schema.sql:257) averages every purchase ever, with no date
    filter, so buying stock in September retroactively rewrote July's profit.
    """
    july_only = run(
        orders=[order(date="2026-07-15T00:00:00+00:00")],
        order_lines=[line(qty=10, revenue=200.0)],
        purchases=[purchase(qty=10, unit=5.0, date="2026-07-01")],
    )
    with_september = run(
        orders=[order(date="2026-07-15T00:00:00+00:00")],
        order_lines=[line(qty=10, revenue=200.0)],
        purchases=[
            purchase(qty=10, unit=5.0, date="2026-07-01", pid="p1"),
            purchase(qty=100, unit=8.0, date="2026-09-01", pid="p2"),
        ],
    )
    assert amounts(july_only, "cogs") == [-50.0]
    assert amounts(with_september, "cogs") == [-50.0], "July COGS moved when September stock arrived"


def test_purchases_settle_before_sales_on_the_same_day():
    result = run(
        orders=[order(date="2026-07-01T00:00:00+00:00")],
        order_lines=[line(qty=2, revenue=50.0)],
        purchases=[purchase(qty=2, unit=7.0, date="2026-07-01")],
    )
    assert amounts(result, "cogs") == [-14.0]


# ----------------------------------------------- unknown is not zero

def test_uncosted_sale_posts_no_cogs_entry():
    result = run(order_lines=[line(qty=3, revenue=90.0)], purchases=[])
    assert amounts(result, "cogs") == []
    assert len(result.uncosted) == 1
    assert result.uncosted[0]["reason"] == "no_purchase_history"


def test_uncosted_is_distinguishable_from_break_even():
    """A zero-profit SKU and an unknown-cost SKU must not look identical."""
    unknown = run(order_lines=[line(qty=1, revenue=10.0)], purchases=[])
    breakeven = run(
        order_lines=[line(qty=1, revenue=10.0)],
        purchases=[purchase(qty=1, unit=10.0, date="2026-06-01")],
    )
    assert unknown.uncosted and not breakeven.uncosted
    assert breakeven.net() == pytest.approx(0.0)
    assert unknown.net() == pytest.approx(10.0)   # revenue only, flagged uncosted


def test_manual_product_cost_is_the_fallback_not_the_default():
    result = run(
        order_lines=[line(qty=2, revenue=60.0)],
        purchases=[],
        product_costs=[{"sku": "SKU1", "unit_cost": 7.0, "warehouse_cost": 0}],
    )
    assert amounts(result, "cogs") == [-14.0]
    assert not result.uncosted


def test_landed_cost_takes_precedence_over_manual():
    result = run(
        order_lines=[line(qty=1, revenue=60.0)],
        purchases=[purchase(qty=1, unit=4.0, date="2026-06-01")],
        product_costs=[{"sku": "SKU1", "unit_cost": 99.0, "warehouse_cost": 0}],
    )
    assert amounts(result, "cogs") == [-4.0]


# ------------------------------------------------------- revenue rules

def test_shipping_the_customer_pays_is_revenue():
    """migration_shipping_fix.sql: it used to be subtracted as if it were a cost."""
    result = run(order_lines=[line(revenue=100.0, shipping=12.0)])
    assert amounts(result, "revenue_shipping") == [12.0]
    assert all(a > 0 for a in amounts(result, "revenue_shipping"))


def test_cancelled_lines_post_nothing():
    result = run(order_lines=[line(status="Cancelled", revenue=99.0)])
    assert result.entries == []
    assert result.uncosted == []


@pytest.mark.parametrize("status", ["Created", "Acknowledged"])
def test_unrealized_statuses_post_nothing(status):
    assert run(order_lines=[line(status=status)]).entries == []


@pytest.mark.parametrize("status", ["Shipped", "Delivered", "shipped", "DELIVERED"])
def test_realized_statuses_post_revenue(status):
    assert amounts(run(order_lines=[line(status=status, revenue=42.0)]),
                   "revenue_product") == [42.0]


def test_recognition_uses_ship_date_when_available():
    d = recognition_date(line(shipped_at="2026-08-03T10:00:00+00:00"),
                         order(date="2026-07-30T00:00:00+00:00"))
    assert d == dt.date(2026, 8, 3)


def test_recognition_falls_back_to_order_date():
    assert recognition_date(line(), order(date="2026-07-30T00:00:00+00:00")) == dt.date(2026, 7, 30)


# ------------------------------------------------------------- returns

def test_return_reverses_in_the_return_month_not_the_sale_month():
    result = post(
        store_id="S",
        orders=[order(po="PO1", date="2026-07-10T00:00:00+00:00"),
                order(po="PO2", date="2026-09-05T00:00:00+00:00")],
        order_lines=[
            line(po="PO1", n="1", qty=1, revenue=100.0, status="Shipped"),
            line(po="PO2", n="1", qty=1, revenue=100.0, status="Returned"),
        ],
        purchases=[purchase(qty=5, unit=10.0, date="2026-06-01")],
        product_costs=[],
    )
    july = [e for e in result.entries if e["entry_date"].startswith("2026-07")]
    sept = [e for e in result.entries if e["entry_date"].startswith("2026-09")]
    assert round(sum(e["amount"] for e in july), 2) == 90.0   # 100 revenue - 10 cogs
    assert any(e["entry_type"] == "return_revenue" for e in sept)
    assert not any(e["entry_type"] == "return_revenue" for e in july)


# ---------------------------------------------------------------- fees

FEE_MAP = [
    {"amount_type": "Product Price", "category": "ignore"},
    {"amount_type": "Shipping", "category": "ignore"},
    {"amount_type": "Commission on Product", "category": "walmart_fee"},
    {"amount_type": "Fee/Reimbursement", "category": "wfs_label_fee"},
]


def settle(amount_type, amount, cid="C1", sku="SKU1", report="2026-07-28"):
    return {"amount_type": amount_type, "amount": amount, "customer_order_id": cid,
            "sku": sku, "report_date": report}


def test_revenue_amount_types_are_ignored_not_posted_as_fees():
    """The +$7,100 phantom-profit bug: unmapped revenue became a fee."""
    result = run(settlement_lines=[settle("Product Price", 440.62),
                                   settle("Shipping", 120.00)],
                 fee_category_map=FEE_MAP)
    assert amounts(result, "fee_other") == []
    assert result.totals().get("fee_referral") is None


def test_unmapped_amount_type_is_not_posted():
    result = run(settlement_lines=[settle("Some New Walmart Fee", -50.0)],
                 fee_category_map=FEE_MAP)
    assert not [e for e in result.entries if e["source_kind"] == "settlement"]


def test_mapped_fees_post_with_their_sign_preserved():
    result = run(settlement_lines=[settle("Commission on Product", -9.32),
                                   settle("Fee/Reimbursement", -7.48)],
                 fee_category_map=FEE_MAP)
    assert amounts(result, "fee_referral") == [-9.32]
    assert amounts(result, "fee_label") == [-7.48]


# ------------------------------------------------------- real regression

def test_swiftzar107_matches_the_hand_verified_figure():
    """From migration_dashboard_net_profit.sql: the dashboard reported $42.69
    when the seller actually made $24.39.

        77.69 revenue - 35.00 cogs - 1.50 warehouse - 9.32 referral - 7.48 label
    """
    result = post(
        store_id="S",
        orders=[order(customer="CO7")],
        order_lines=[line(sku="SWIFTZAR107", qty=1, revenue=77.69, status="Shipped")],
        purchases=[purchase(sku="SWIFTZAR107", qty=1, unit=35.0, date="2026-06-15")],
        product_costs=[{"sku": "SWIFTZAR107", "unit_cost": 35.0, "warehouse_cost": 1.5}],
        settlement_lines=[settle("Commission on Product", -9.32, cid="CO7", sku="SWIFTZAR107"),
                          settle("Fee/Reimbursement", -7.48, cid="CO7", sku="SWIFTZAR107")],
        fee_category_map=FEE_MAP,
    )
    assert result.net() == pytest.approx(24.39)


# ------------------------------------------------------- determinism

def test_posting_is_deterministic():
    kw = dict(
        store_id="S",
        orders=[order(po=f"PO{i}") for i in range(5)],
        order_lines=[line(po=f"PO{i}", qty=i + 1, revenue=10.0 * (i + 1)) for i in range(5)],
        purchases=[purchase(qty=50, unit=3.0, date="2026-06-01")],
        product_costs=[],
    )
    assert post(**kw).entries == post(**kw).entries


def test_reordering_inputs_does_not_change_output():
    kw = dict(
        store_id="S",
        orders=[order(po="PO1", date="2026-07-01T00:00:00+00:00"),
                order(po="PO2", date="2026-07-02T00:00:00+00:00")],
        order_lines=[line(po="PO1", n="1", qty=1, revenue=10.0),
                     line(po="PO2", n="1", qty=1, revenue=20.0)],
        purchases=[purchase(qty=5, unit=2.0, date="2026-06-01", pid="p1"),
                   purchase(qty=5, unit=4.0, date="2026-06-02", pid="p2")],
        product_costs=[],
    )
    forward = post(**kw)
    shuffled = dict(kw)
    shuffled["order_lines"] = list(reversed(kw["order_lines"]))
    shuffled["purchases"] = list(reversed(kw["purchases"]))
    assert sorted(e["amount"] for e in forward.entries) == \
           sorted(e["amount"] for e in post(**shuffled).entries)

def test_repeated_settlement_rows_aggregate_into_one_entry():
    """Walmart sends a label charge and its adjustment as separate rows sharing
    the same (report, order, sku, amount_type). They are one economic fact, and
    posting them separately collides on the ledger natural key."""
    result = run(
        settlement_lines=[
            settle("Fee/Reimbursement", -6.87, cid="C1", sku="SKU1"),
            settle("Fee/Reimbursement", 0.20, cid="C1", sku="SKU1"),
        ],
        fee_category_map=FEE_MAP,
    )
    fees = [e for e in result.entries if e["entry_type"] == "fee_label"]
    assert len(fees) == 1
    assert fees[0]["amount"] == pytest.approx(-6.67)


def test_settlement_entries_have_unique_natural_keys():
    """Mirrors the ledger_entries_natural_key constraint."""
    result = run(
        settlement_lines=[
            settle("Fee/Reimbursement", -6.87, cid="C1", sku="SKU1"),
            settle("Fee/Reimbursement", 0.20, cid="C1", sku="SKU1"),
            settle("Commission on Product", -2.00, cid="C1", sku="SKU1"),
        ],
        fee_category_map=FEE_MAP,
    )
    keys = [(e["source_kind"], e["source_id"], e["entry_type"]) for e in result.entries]
    assert len(keys) == len(set(keys))


def test_fees_netting_to_zero_are_not_posted():
    result = run(
        settlement_lines=[settle("Fee/Reimbursement", -5.00, cid="C1"),
                          settle("Fee/Reimbursement", 5.00, cid="C1")],
        fee_category_map=FEE_MAP,
    )
    assert [e for e in result.entries if e["entry_type"] == "fee_label"] == []
