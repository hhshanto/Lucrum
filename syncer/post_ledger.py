"""Post mirrors + seller facts into the append-only ledger.

This is the thin, impure shell around posting.py. Everything here is I/O --
fetch rows, hand them to the pure functions, write what comes back. All the
money rules live in posting.py, where they are unit-tested.

Usage:
    python post_ledger.py                 # post anything not yet posted
    python post_ledger.py --dry-run       # compute and compare, write nothing
"""

import argparse
import os

from dotenv import load_dotenv
from supabase import create_client

from posting import post

load_dotenv()

PAGE = 500


def fetch_all(sb, table, columns="*", **eq):
    """Page through a table.

    PostgREST silently caps an unqualified select at 1000 rows (EDGE_CASES.md
    Q1). settlement_lines alone is already 2,290, so paging is not optional --
    a single select here would quietly cost us more than half the fee data.
    """
    out, offset = [], 0
    while True:
        q = sb.table(table).select(columns)
        for key, value in eq.items():
            q = q.eq(key, value)
        batch = q.range(offset, offset + PAGE - 1).execute().data
        out += batch
        if len(batch) < PAGE:
            return out
        offset += PAGE


def main():
    parser = argparse.ArgumentParser(description="Post facts into ledger_entries")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and report, but write nothing")
    parser.add_argument("--rebuild", action="store_true",
                        help="discard the existing ledger first. Required after "
                             "changing how any figure is derived: posting is "
                             "idempotent, so an entry built from a wrong input "
                             "is skipped rather than replaced.")
    parser.add_argument("--store-id")
    args = parser.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    stores = sb.table("stores").select("store_id, name").eq("active", True)
    if args.store_id:
        stores = stores.eq("store_id", args.store_id)

    for store in stores.execute().data:
        store_id, name = store["store_id"], store["name"]
        print(f"Posting {name!r} ({store_id})...")

        if args.rebuild and not args.dry_run:
            wiped = sb.rpc("rebuild_ledger", {"p_store_id": store_id}).execute().data
            if wiped:
                print(f"  rebuild: cleared {wiped[0]['deleted_entries']} entries, "
                      f"{wiped[0]['deleted_uncosted']} uncosted")

        orders = fetch_all(sb, "orders",
                           "purchase_order_id,customer_order_id,order_date",
                           store_id=store_id)
        lines = fetch_all(sb, "order_lines",
                          "purchase_order_id,line_number,sku,quantity,"
                          "product_revenue,shipping_revenue,status,shipped_at",
                          store_id=store_id)
        purchases = fetch_all(sb, "purchases",
                              "id,sku,quantity,unit_cost,sales_tax,shipping,"
                              "purchase_date,created_at", store_id=store_id)
        costs = fetch_all(sb, "product_costs",
                          "sku,unit_cost,warehouse_cost,is_placeholder",
                          store_id=store_id)
        settlement = fetch_all(sb, "settlement_lines",
                               "report_date,customer_order_id,sku,amount_type,amount",
                               store_id=store_id)
        fee_map = fetch_all(sb, "fee_category_map", "amount_type,category",
                            store_id=store_id)
        line_costs = fetch_all(
            sb, "order_line_costs",
            "source_id,sku,unit_cost,basis,walmart_fee,label_walmart,"
            "label_third_party,warehouse_cost",
            store_id=store_id)

        print(f"  inputs: {len(orders)} orders, {len(lines)} lines, "
              f"{len(purchases)} purchases, {len(settlement)} settlement rows")

        result = post(store_id, orders, lines, purchases, costs, settlement, fee_map,
                      line_costs=line_costs)

        print(f"  {len(result.entries)} entries, {len(result.uncosted)} uncosted lines")
        for entry_type, total in sorted(result.totals().items()):
            print(f"      {entry_type:<18} {total:>12,.2f}")
        print(f"      {'NET':<18} {result.net():>12,.2f}")

        if args.dry_run:
            print("  dry run -- nothing written")
            continue

        # on_conflict MUST name the natural key. Left to itself PostgREST targets
        # the primary key, and entry_id is a fresh uuid on every row -- so it
        # would never collide, and the natural-key constraint would raise
        # instead of being ignored.
        #
        # ignore rather than merge is deliberate: the append-only trigger
        # rejects UPDATE, which is the entire point of the ledger.
        for i in range(0, len(result.entries), 200):
            sb.table("ledger_entries").upsert(
                result.entries[i:i + 200],
                on_conflict="store_id,source_kind,source_id,entry_type",
                ignore_duplicates=True).execute()
        for i in range(0, len(result.uncosted), 200):
            sb.table("uncosted_lines").upsert(
                result.uncosted[i:i + 200],
                on_conflict="store_id,source_id",
                ignore_duplicates=True).execute()
        print(f"  wrote {len(result.entries)} entries / {len(result.uncosted)} uncosted")


if __name__ == "__main__":
    main()
