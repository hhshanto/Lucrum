"""Import exact per-line buying costs from the seller's SWIFTZAR workbooks.

Two books, two layouts, same meaning -- what was actually paid for each sale:

    SWIFTZAR LLC till Aug 8.xlsx   "Unit Purchase Price"  keyed by Customer Order ID
    Swiftzar Llc.xlsx              "Per Item Cost"        keyed by Order#

A spreadsheet row that names a specific customer order and SKU is the cost of
that exact sale, which beats any averaging. Where a line has no row of its own,
the SKU's other rows are used -- but only counted as known if they all agree.

Usage:
    python import_costs.py --dry-run
    python import_costs.py
"""

import argparse
import collections
import os
import re
import statistics

import openpyxl
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# path, sheets, and the header labels for order id / sku / unit cost
BOOKS = [
    ("SWIFTZAR LLC till Aug 8.xlsx",
     ["July 1- July 4", "July 4- Aug 8 ", "Aug 8-Aug 22"],
     "customer order id", "sku/upc", "unit purchase price"),
    ("Swiftzar Llc.xlsx",
     ["Aug-26", "Sep-26", "Oct-26"],
     "order#", "sku code", "per item cost"),
]


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _order_id(value) -> str:
    """Excel stores these as floats, so 200015460384635 arrives as ...635.0."""
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") else text


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sku_variants(value):
    """A UPC typed into Excel loses its leading zeros and gains '.0'.

    '754127508214.0' in the sheet is '00754127508214' in Walmart's data.
    """
    text = str(value or "").strip()
    out = {text, text.lower()}
    if text.endswith(".0"):
        text = text[:-2]
        out |= {text, text.lower()}
    if text.isdigit():
        out |= {text.zfill(14), text.zfill(12)}
    return out


def read_books():
    rows = []
    for filename, sheets, k_order, k_sku, k_cost in BOOKS:
        path = os.path.join(REPO, filename)
        if not os.path.exists(path):
            print(f"  skipping (not found): {filename}")
            continue
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in sheets:
            if sheet not in book.sheetnames:
                continue
            ws, header, count = book[sheet], None, 0
            for row in ws.iter_rows(values_only=True):
                if header is None:
                    labels = [_norm(c) for c in (row or [])]
                    if k_order in labels and k_cost in labels:
                        header = {label: i for i, label in enumerate(labels) if label}
                    continue

                def cell(key):
                    i = header.get(key)
                    return row[i] if i is not None and i < len(row) else None

                order_id = _order_id(cell(k_order))
                sku = str(cell(k_sku) or "").strip()
                cost = _number(cell(k_cost))
                # A cost of 0 is an unfilled cell, not a free product.
                if order_id and sku and cost and cost > 0:
                    rows.append({"customer_order_id": order_id, "sku": sku,
                                 "unit_cost": cost,
                                 "source": f"{filename}#{sheet}"})
                    count += 1
            print(f"  {filename:32} [{sheet:16}] {count:4} rows")
        book.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description="Import per-line costs from the workbooks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    store_id = sb.table("stores").select("store_id").eq("active", True).execute().data[0]["store_id"]

    sheet_rows = read_books()
    print(f"  parsed {len(sheet_rows)} usable rows\n")

    def fetch_all(table, columns):
        out, offset = [], 0
        while True:
            batch = (sb.table(table).select(columns).eq("store_id", store_id)
                     .range(offset, offset + 499).execute().data)
            out += batch
            if len(batch) < 500:
                return out
            offset += 500

    orders = fetch_all("orders", "purchase_order_id,customer_order_id")
    lines = fetch_all("order_lines", "purchase_order_id,line_number,sku,status")

    customer_order = {o["purchase_order_id"]: o["customer_order_id"] for o in orders}
    db_skus = {(l["sku"] or "").strip() for l in lines}
    by_lower = {s.lower(): s for s in db_skus}

    def resolve(sku):
        for variant in sku_variants(sku):
            if variant in db_skus:
                return variant
            if variant.lower() in by_lower:
                return by_lower[variant.lower()]
        return None

    exact = {}
    per_sku = collections.defaultdict(list)
    for row in sheet_rows:
        target = resolve(row["sku"])
        if not target:
            continue
        exact[(row["customer_order_id"], target)] = row
        per_sku[target].append(row["unit_cost"])

    realized = [l for l in lines
                if (l["status"] or "").strip().lower() in ("shipped", "delivered")]

    out, tally = [], collections.Counter()
    for line in realized:
        sku = (line["sku"] or "").strip()
        key = (customer_order.get(line["purchase_order_id"]), sku)
        source_id = f"{line['purchase_order_id']}:{line['line_number']}"
        match = exact.get(key)
        if match:
            basis, cost, source = "exact", match["unit_cost"], match["source"]
        elif per_sku.get(sku):
            costs = per_sku[sku]
            # Every row agreeing means the cost is not actually in doubt.
            basis = "sku_single" if len(set(costs)) == 1 else "sku_imputed"
            cost = statistics.median(costs)
            source = f"derived from {len(costs)} row(s) for {sku}"
        else:
            tally["no_data"] += 1
            continue
        tally[basis] += 1
        out.append({"store_id": store_id, "source_id": source_id, "sku": sku,
                    "unit_cost": round(cost, 4), "basis": basis, "source": source})

    print(f"  realized lines            : {len(realized)}")
    for basis in ("exact", "sku_single", "sku_imputed", "no_data"):
        print(f"    {basis:<12} {tally[basis]:>4}")

    if args.dry_run:
        print("\n  dry run -- nothing written")
        return

    for i in range(0, len(out), 200):
        sb.table("order_line_costs").upsert(out[i:i + 200]).execute()
    print(f"\n  wrote {len(out)} order_line_costs rows")


if __name__ == "__main__":
    main()
