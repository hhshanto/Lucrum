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
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from supabase import create_client

from posting import post

load_dotenv()

PAGE = 500

# Rows per upsert. The work here is not computation -- building every figure for
# this store takes about ten milliseconds -- it is round trips. At 200 a rebuild
# spent four seconds queueing eighteen requests that each carried a trivial
# payload, so the batch is sized to the network rather than to the database.
WRITE_BATCH = 1000

# The nine input tables do not depend on each other, so waiting for each in turn
# was two and a half seconds of mostly idle time. Fetched together the cost is
# the slowest single table instead of the sum of all of them.
FETCH_WORKERS = 9


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


INPUT_TABLES = {
    "orders": ("orders", "purchase_order_id,customer_order_id,order_date"),
    "lines": ("order_lines", "purchase_order_id,line_number,sku,quantity,"
                             "product_revenue,shipping_revenue,status,shipped_at"),
    "purchases": ("purchases", "id,sku,quantity,unit_cost,sales_tax,shipping,"
                               "purchase_date,created_at"),
    "costs": ("product_costs", "sku,unit_cost,warehouse_cost,is_placeholder"),
    "settlement": ("settlement_lines", "report_date,customer_order_id,sku,amount_type,"
                                       "amount,transaction_type,transaction_posted_at"),
    "fee_map": ("fee_category_map", "transaction_type,amount_type,category"),
    "line_costs": ("order_line_costs", "source_id,sku,unit_cost,basis,walmart_fee,"
                                       "label_walmart,label_third_party,warehouse_cost"),
    "calibration": ("fee_calibration", "entry_type,pairs,ratio_median,ratio_p10,ratio_p90"),
}


def fetch_inputs(sb, store_id):
    """Read every input table at once.

    httpx's client is thread-safe and each query builds its own request, so the
    shared Supabase client is safe to use across these threads.
    """
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {
            key: pool.submit(fetch_all, sb, table, columns, store_id=store_id)
            for key, (table, columns) in INPUT_TABLES.items()
        }
        return {key: f.result() for key, f in futures.items()}


def write_result(sb, result):
    for i in range(0, len(result.entries), WRITE_BATCH):
        sb.table("ledger_entries").upsert(
            result.entries[i:i + WRITE_BATCH],
            on_conflict="store_id,source_kind,source_id,entry_type",
            ignore_duplicates=True).execute()
    for i in range(0, len(result.uncosted), WRITE_BATCH):
        sb.table("uncosted_lines").upsert(
            result.uncosted[i:i + WRITE_BATCH],
            on_conflict="store_id,source_id",
            ignore_duplicates=True).execute()


def rebuild_store(sb, store_id, name="", *, rebuild=False, dry_run=False, log=print):
    """Post one store. Shared by the CLI and the web server's /post endpoint.

    The server used to reach this by spawning a new interpreter, which cost a
    second or two of startup to run ten milliseconds of arithmetic. Calling it
    directly removes that, and lets the endpoint report what happened instead of
    scraping stdout.
    """
    log(f"Posting {name or store_id!r} ({store_id})...")

    if rebuild and not dry_run:
        wiped = sb.rpc("rebuild_ledger", {"p_store_id": store_id}).execute().data
        if wiped:
            log(f"  rebuild: cleared {wiped[0]['deleted_entries']} entries, "
                f"{wiped[0]['deleted_uncosted']} uncosted")

    data = fetch_inputs(sb, store_id)
    calibration = {r["entry_type"]: r for r in data["calibration"]}

    log(f"  inputs: {len(data['orders'])} orders, {len(data['lines'])} lines, "
        f"{len(data['purchases'])} purchases, {len(data['settlement'])} settlement rows")

    if not calibration:
        # Silence here used to be indistinguishable from "no adjustment
        # needed", which hid a circular dependency on the ledger for a whole
        # run. Say so out loud.
        log("  calibration: NONE AVAILABLE -- workbook fees post unadjusted")
    for entry_type, row in sorted(calibration.items()):
        log(f"  calibration: {entry_type:<13} ratio {row['ratio_median']} "
            f"on {row['pairs']} settled pairs")

    result = post(store_id, data["orders"], data["lines"], data["purchases"],
                  data["costs"], data["settlement"], data["fee_map"],
                  line_costs=data["line_costs"], fee_calibration=calibration)

    log(f"  {len(result.entries)} entries, {len(result.uncosted)} uncosted lines")
    for entry_type, total in sorted(result.totals().items()):
        log(f"      {entry_type:<18} {total:>12,.2f}")
    log(f"      {'NET':<18} {result.net():>12,.2f}")

    if dry_run:
        log("  dry run -- nothing written")
        return result

    # on_conflict MUST name the natural key. Left to itself PostgREST targets
    # the primary key, and entry_id is a fresh uuid on every row -- so it
    # would never collide, and the natural-key constraint would raise
    # instead of being ignored.
    #
    # ignore rather than merge is deliberate: the append-only trigger
    # rejects UPDATE, which is the entire point of the ledger.
    write_result(sb, result)
    log(f"  wrote {len(result.entries)} entries / {len(result.uncosted)} uncosted")
    return result


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
        rebuild_store(sb, store["store_id"], store["name"],
                      rebuild=args.rebuild, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
