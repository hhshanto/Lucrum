"""Pure posting logic: turns mirrors + seller facts into ledger entries.

NO network. NO database. Every function here is pure -- plain dicts in, plain
dicts out -- which is the entire point. Ingestion is messy and external;
posting is deterministic and can be unit-tested with fixtures. See
ORDER_FLOW.md section 1.

The money rules implemented here are specified in DATA_MODEL.md:
  * COGS is a moving average over purchases BEFORE the sale, frozen at the sale
  * A sale with no cost basis posts NO cogs entry and is recorded as uncosted
    -- unknown is not zero
  * Shipping the customer pays is revenue; the label is a fee Walmart deducts
  * Only Shipped/Delivered lines realize
  * Returns reverse in the return's month, never by restating the original
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# What identifies an entry, and what an entry claims about money.
#
# post_ledger compares exactly these fields when deciding whether a stored row
# still matches the computation, so it can rewrite only what moved instead of
# the whole ledger. A full wipe could not leave a stale row -- it deleted
# everything -- and the partial path only keeps that guarantee while this list
# stays complete. Add a field to _entry without adding it here and the ledger
# goes quietly stale, so a test asserts the two agree.
#
# entry_id and posted_at are excluded on purpose: both change on every compute
# and neither is a fact about the money. effective_for and reverses are set by
# the database, never by posting.
ENTRY_KEY_FIELDS = ("source_kind", "source_id", "entry_type")
ENTRY_VALUE_FIELDS = ("entry_date", "sku", "quantity", "amount", "note")

# Walmart statuses that mean "this sale is real and Walmart will pay for it".
REALIZED_STATUSES = frozenset({"shipped", "delivered"})
RETURN_STATUSES = frozenset({"returned", "refunded"})
CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})

# fee_category_map.category -> ledger entry_type.
#
# 'return_revenue' is why the map is keyed on (transaction_type, amount_type):
# 'Product Price' on a Sale duplicates order_lines and must be ignored, while
# the same label on a Refund reverses revenue and must be posted. One key cannot
# express two opposite meanings.
FEE_CATEGORY_TO_ENTRY = {
    "walmart_fee": "fee_referral",
    "wfs_label_fee": "fee_label",
    "refund_fee": "fee_refund",
    "extra_service_fee": "fee_other",
    "return_revenue": "return_revenue",
}


def norm_status(status) -> str:
    return (status or "").strip().lower()


def is_realized(status) -> bool:
    return norm_status(status) in REALIZED_STATUSES


def is_return(status) -> bool:
    return norm_status(status) in RETURN_STATUSES


def source_id(line: dict) -> str:
    """Stable natural key for an order line."""
    return f"{line['purchase_order_id']}:{line['line_number']}"


# --------------------------------------------------------------------------
# Cost basis: moving average at time of sale
# --------------------------------------------------------------------------

@dataclass
class CostBasis:
    """Running moving-average state for ONE sku.

    Deliberately not rounded between steps: rounding is presentation, and
    rounding the running average would drift over hundreds of events.
    """

    qty: float = 0.0
    value: float = 0.0

    @property
    def unit_cost(self):
        return (self.value / self.qty) if self.qty > 0 else None

    def buy(self, quantity: float, total_cost: float) -> None:
        self.qty += quantity
        self.value += total_cost

    def sell(self, quantity: float):
        """Consume `quantity` at the current average.

        Returns (cogs, reason). cogs is None when there is no basis at all.
        `reason` is None on a clean consume, else an explanation for
        uncosted_lines.
        """
        unit = self.unit_cost
        if unit is None:
            return None, "no_purchase_history"

        reason = "insufficient_qty" if quantity > self.qty else None
        cogs = quantity * unit
        self.qty -= quantity
        self.value -= cogs
        return cogs, reason

    def restock(self, quantity: float, cogs: float) -> None:
        """A return putting goods back at the cost they left at."""
        self.qty += quantity
        self.value += cogs


def landed_total(purchase: dict) -> float:
    """What a purchase row actually cost, all in.

    Matches sku_landed_cost (schema.sql:257) so switching to the ledger does
    not silently change the definition of cost.
    """
    return (
        (purchase["quantity"] or 0) * (purchase["unit_cost"] or 0)
        + (purchase.get("sales_tax") or 0)
        + (purchase.get("shipping") or 0)
    )


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text).date()
    except ValueError:
        return dt.date.fromisoformat(text[:10])


# --------------------------------------------------------------------------
# Recognition date
# --------------------------------------------------------------------------

MIN_CALIBRATION_PAIRS = 20


def _calibrate(amount, entry_type, calibration):
    """Scale a workbook fee by the ratio observed against settled lines.

    Returns (amount, note). Below MIN_CALIBRATION_PAIRS the sample is too small
    to trust, so the figure is posted unadjusted rather than corrected by noise.
    """
    row = (calibration or {}).get(entry_type)
    if not row or (row.get("pairs") or 0) < MIN_CALIBRATION_PAIRS:
        return amount, "workbook estimate, uncalibrated"
    ratio = row.get("ratio_median") or 0
    if ratio <= 0:
        return amount, "workbook estimate, uncalibrated"
    return (amount / float(ratio),
            f"workbook estimate / {float(ratio):.4f} "
            f"(calibrated on {row['pairs']} settled lines)")


def recognition_date(line: dict, order: dict):
    """When a line hits the P&L.

    Ship date, not order date (ORDER_FLOW.md section 3): revenue is earned on
    shipment and Walmart settles on shipment. Falls back to order date when the
    ship timestamp is unavailable, which the caller should surface rather than
    silently assume.
    """
    return _as_date(line.get("shipped_at") or order.get("order_date"))


# --------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------

@dataclass
class PostingResult:
    entries: list = field(default_factory=list)
    uncosted: list = field(default_factory=list)

    def totals(self) -> dict:
        out = {}
        for e in self.entries:
            out[e["entry_type"]] = round(out.get(e["entry_type"], 0.0) + e["amount"], 2)
        return out

    def net(self) -> float:
        return round(sum(e["amount"] for e in self.entries), 2)


def _entry(store_id, date, entry_type, amount, *, sku=None, quantity=None,
           source_kind="order_line", src=None, note=None):
    return {
        "store_id": store_id,
        "entry_date": date.isoformat() if hasattr(date, "isoformat") else date,
        "entry_type": entry_type,
        "sku": sku,
        "quantity": quantity,
        "amount": round(amount, 2),
        "source_kind": source_kind,
        "source_id": src,
        "note": note,
    }


def post(store_id, orders, order_lines, purchases, product_costs,
         settlement_lines=(), fee_category_map=(), line_costs=(),
         fee_calibration=None) -> PostingResult:
    """Build every ledger entry implied by the given facts.

    Pure. Call it twice with the same inputs and you get the same output, which
    is what makes the numbers reproducible.
    """
    result = PostingResult()
    orders_by_po = {o["purchase_order_id"]: o for o in orders}
    costs_by_sku = {c["sku"]: c for c in product_costs}
    # Specific identification: the actual cost of the actual item sold. For
    # buy-to-order arbitrage this beats any averaging, so it outranks the
    # moving average from purchases.
    line_cost_by_source = {c["source_id"]: c for c in line_costs}

    # Which (customer order, sku) pairs settlement already covers. Settlement is
    # what actually left the account, so it wins; the workbook fills gaps only.
    # October has no recon file yet, which is exactly the gap this closes.
    fee_map_by_key = {((m.get("transaction_type") or "Sale"), m["amount_type"]): m["category"]
                      for m in fee_category_map}
    settled = {"walmart_fee": set(), "wfs_label_fee": set()}
    for row in settlement_lines:
        key = ((row.get("transaction_type") or "Sale").strip(), row.get("amount_type") or "")
        category = fee_map_by_key.get(key)
        if category in settled and (row.get("amount") or 0):
            settled[category].add((row.get("customer_order_id"), row.get("sku")))

    # --- build one chronological event stream per sku -------------------
    events = []
    for p in purchases:
        events.append((_as_date(p["purchase_date"]), 0, p.get("created_at") or "",
                       str(p.get("id") or ""), "purchase", p))

    for line in order_lines:
        order = orders_by_po.get(line["purchase_order_id"])
        if order is None:
            continue
        date = recognition_date(line, order)
        if date is None:
            continue
        status = norm_status(line.get("status"))
        if status in CANCELLED_STATUSES:
            continue                      # never realized, so nothing to post
        if is_realized(status):
            events.append((date, 1, "", source_id(line), "sale", (line, order)))
        elif is_return(status):
            events.append((date, 1, "", source_id(line), "return", (line, order)))

    # Purchases sort before sales on the same day (index 1): goods must be on
    # hand before they can be sold. The trailing keys make the walk fully
    # deterministic -- see DATA_MODEL.md section 5.
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    basis: dict[str, CostBasis] = {}
    cogs_by_source: dict[str, float] = {}

    for date, _rank, _created, _id, kind, payload in events:
        if kind == "purchase":
            p = payload
            basis.setdefault(p["sku"], CostBasis()).buy(
                p["quantity"] or 0, landed_total(p))
            continue

        line, _order = payload
        sku = line["sku"]
        qty = line.get("quantity") or 0
        src = source_id(line)
        b = basis.setdefault(sku, CostBasis())

        if kind == "sale":
            result.entries.append(_entry(
                store_id, date, "revenue_product", line.get("product_revenue") or 0,
                sku=sku, quantity=qty, src=src))
            if line.get("shipping_revenue"):
                result.entries.append(_entry(
                    store_id, date, "revenue_shipping", line["shipping_revenue"],
                    sku=sku, quantity=qty, src=src))

            def flag(reason):
                result.uncosted.append({
                    "store_id": store_id, "source_id": src, "sku": sku,
                    "quantity": qty, "sale_date": date.isoformat(),
                    "reason": reason,
                })

            charges = line_cost_by_source.get(src) or {}
            customer_order = _order.get("customer_order_id")

            def post_warehouse():
                """Handling charge. Per ORDER, not per unit -- the workbook
                charges 1.50 on a six-unit line. product_costs stores a per-unit
                figure, so it is only the fallback."""
                if charges.get("warehouse_cost") is not None:
                    amount = charges["warehouse_cost"]
                else:
                    amount = qty * ((costs_by_sku.get(sku) or {}).get("warehouse_cost") or 0)
                if amount:
                    result.entries.append(_entry(
                        store_id, date, "warehouse", -amount,
                        sku=sku, quantity=qty, src=src))

            # Third-party labels are bought outside Walmart, so they appear in
            # no API. The workbook is the ONLY source -- there is nothing to
            # defer to and nothing to reconcile against.
            if charges.get("label_third_party"):
                result.entries.append(_entry(
                    store_id, date, "fee_label_3p", -charges["label_third_party"],
                    sku=sku, quantity=qty, source_kind="manual", src=src,
                    note="third-party label (never in settlement)"))

            # Walmart-side charges. Post from the workbook ONLY where settlement
            # has nothing, or they would double-count.
            #
            # These two columns are transcribed from Walmart by hand and the
            # seller reports they are not reliably entered -- across 84 lines the
            # workbook's label cost exceeds settlement by a consistent 2-3%. So a
            # workbook-sourced Walmart charge is an ESTIMATE, never a known
            # figure, and the line is flagged accordingly. Settlement supersedes
            # it automatically once the recon file arrives.
            # Calibrated against every line where both a workbook figure and a
            # settled figure exist. The workbook bills referral about 25% above
            # what Walmart charges -- 366 pairs spread over just 0.6% -- so
            # posting it at face value would overstate the fee badly. Dividing
            # by the observed ratio is a far better estimate of the real charge.
            workbook_fee_used = False
            if charges.get("label_walmart") and (customer_order, sku) not in settled["wfs_label_fee"]:
                amount, note = _calibrate(charges["label_walmart"], "fee_label", fee_calibration)
                result.entries.append(_entry(
                    store_id, date, "fee_label", -amount,
                    sku=sku, quantity=qty, source_kind="manual", src=src, note=note))
                workbook_fee_used = True
            if charges.get("walmart_fee") and (customer_order, sku) not in settled["walmart_fee"]:
                amount, note = _calibrate(charges["walmart_fee"], "fee_referral", fee_calibration)
                result.entries.append(_entry(
                    store_id, date, "fee_referral", -amount,
                    sku=sku, quantity=qty, source_kind="manual", src=src, note=note))
                workbook_fee_used = True
            if workbook_fee_used:
                flag("estimated_fee")

            exact = line_cost_by_source.get(src) if charges.get("unit_cost") else None
            if exact:
                # Known actual cost for THIS line. Still consume inventory so
                # the running average stays honest for lines that fall through
                # to it.
                b.sell(qty)
                cogs = qty * exact["unit_cost"]
                result.entries.append(_entry(
                    store_id, date, "cogs", -cogs, sku=sku, quantity=qty, src=src,
                    note=f"line cost ({exact.get('basis')})"))
                cogs_by_source[src] = cogs
                if exact.get("basis") == "sku_imputed":
                    # The SKU's recorded costs disagree, so this line's figure
                    # is a median rather than its own price.
                    flag("estimated_cost")
                post_warehouse()
                continue

            cogs, reason = b.sell(qty)
            if cogs is None:
                manual = costs_by_sku.get(sku) or {}
                unit = manual.get("unit_cost") or 0
                if unit > 0:
                    # A placeholder still posts COGS -- otherwise the P&L is
                    # empty -- but it is flagged, so the number never renders as
                    # if it were known. Estimated is a third state, distinct
                    # from both known and unknown.
                    estimated = bool(manual.get("is_placeholder"))
                    cogs = qty * unit
                    result.entries.append(_entry(
                        store_id, date, "cogs", -cogs, sku=sku, quantity=qty, src=src,
                        note="estimated placeholder cost" if estimated else "manual unit cost"))
                    cogs_by_source[src] = cogs
                    if estimated:
                        flag("estimated_cost")
                elif manual:
                    # A stored cost of 0.00 is a row somebody never filled in,
                    # not a free product. Treating it as known would report the
                    # entire sale price as profit.
                    flag("zero_cost")
                else:
                    flag("no_purchase_history")
            else:
                result.entries.append(_entry(
                    store_id, date, "cogs", -cogs, sku=sku, quantity=qty, src=src,
                    note=reason))
                cogs_by_source[src] = cogs
                if reason:
                    flag(reason)

            post_warehouse()

        elif kind == "return":
            # Reverse in the return's month. January keeps January's number.
            result.entries.append(_entry(
                store_id, date, "return_revenue", -(line.get("product_revenue") or 0),
                sku=sku, quantity=qty, src=src, note="return"))
            original = cogs_by_source.get(src)
            if original:
                result.entries.append(_entry(
                    store_id, date, "return_cogs", original,
                    sku=sku, quantity=qty, src=src, note="return"))
                b.restock(qty, original)

    result.entries.extend(
        post_fees(store_id, orders, order_lines, settlement_lines, fee_category_map))
    return result


def post_fees(store_id, orders, order_lines, settlement_lines, fee_category_map):
    """Fees from settlement, bucketed via the per-store map.

    An unmapped amount_type is NOT posted -- it is quarantined (DATA_MODEL.md
    section 8). Guessing is how 'Product Price' became a fee and doubled revenue.
    """
    # Keyed on the pair. Rows predating the re-key carry no transaction_type and
    # are treated as sale rules, which is what they were written to mean.
    mapping = {((m.get("transaction_type") or "Sale"), m["amount_type"]): m["category"]
               for m in fee_category_map}
    orders_by_po = {o["purchase_order_id"]: o for o in orders}

    date_by_customer_order = {}
    for line in order_lines:
        order = orders_by_po.get(line["purchase_order_id"])
        if not order or not is_realized(line.get("status")):
            continue
        d = recognition_date(line, order)
        cid = order.get("customer_order_id")
        if cid and d:
            date_by_customer_order.setdefault(cid, d)

    # Walmart emits SEVERAL rows for the same (report, order, sku, amount_type)
    # -- a label charge of -6.87 and a +0.20 adjustment to it arrive separately.
    # They are one economic fact, so they must aggregate into one entry.
    # Posting them individually collides on the ledger's natural key.
    buckets = {}
    for row in settlement_lines:
        amount_type = row.get("amount_type") or ""
        transaction = (row.get("transaction_type") or "").strip()

        # Looked up on the FULL key. An unrecognised combination is not guessed
        # at -- it is skipped and surfaced by unmapped_amount_types, because
        # guessing is how 'Product Price' became a fee and doubled revenue.
        category = mapping.get((transaction, amount_type))
        if category is None or category == "ignore":
            continue
        entry_type = FEE_CATEGORY_TO_ENTRY.get(category)
        if entry_type is None:
            continue

        transaction = transaction.lower()
        cid = row.get("customer_order_id")

        # transaction_type belongs in the key: a Sale and a Refund for the same
        # order, sku and amount_type can share a report date.
        src = f"{row.get('report_date')}:{transaction or 'sale'}:{cid}:{row.get('sku')}:{amount_type}"
        key = (src, entry_type)
        if key not in buckets:
            if transaction == "refund":
                # A refund is a NEW event, not a correction to the sale. It
                # belongs in the month it happened, so January keeps the profit
                # January reported (DATA_MODEL.md section 6).
                date = _as_date(row.get("transaction_posted_at")) or _as_date(row.get("report_date"))
            else:
                # Economic date when we can tie it to a sale, else report date.
                date = date_by_customer_order.get(cid) or _as_date(row.get("report_date"))
            buckets[key] = {"date": date, "sku": row.get("sku"), "amount": 0.0}
        buckets[key]["amount"] += row.get("amount") or 0

    entries = []
    for (src, entry_type), b in sorted(buckets.items()):
        if b["date"] is None or round(b["amount"], 2) == 0:
            continue
        entries.append(_entry(
            store_id, b["date"], entry_type, b["amount"], sku=b["sku"],
            source_kind="settlement", src=src))
    return entries
