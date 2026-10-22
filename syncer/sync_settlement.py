"""Pulls Walmart's recon (settlement) report into settlement_lines.

This is the source of truth for seller-side fees (referral/category fees,
WFS fulfillment/storage fees, refund processing fees, etc) that don't appear
on the Orders API. Run this periodically (e.g. daily) alongside sync.py.

The exact "Amount Type" / "Transaction Type" strings Walmart returns vary by
account, so this script just stores the raw rows. Posting then buckets them via
fee_category_map, which is keyed on (transaction_type, amount_type) because the
same label means opposite things depending on the transaction: "Product Price"
on a Sale duplicates order revenue, while on a Refund it reverses it.

After a sync, check the unmapped_amount_types view. It must be empty -- an
unrecognised combination is deliberately NOT posted rather than guessed at.

Usage:
    python sync_settlement.py --days 7
"""

import argparse
import datetime as dt
import os
import sys

from dotenv import load_dotenv
from supabase import create_client

from secrets import decrypt
from walmart_client import WalmartClient

load_dotenv()

MAX_DAYS = 90

# Comfortably above the 300s subprocess cap in web/app.py, so a live run is never
# stolen mid-flight. The cost is a bounded wait if the syncer is hard-killed; see
# migration_a24_sync_lock.sql.
LOCK_TTL_SECONDS = 900


def _field(row: dict, *names):
    for name in names:
        if name in row:
            return row[name]

    normalized = {key.strip().lower().replace(" ", "").replace("_", ""): value for key, value in row.items()}
    for name in names:
        key = name.strip().lower().replace(" ", "").replace("_", "")
        if key in normalized:
            return normalized[key]

    return None


def _parse_timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).isoformat()
    try:
        return dt.datetime.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def _to_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_report_date(value) -> dt.date:
    """Walmart returns recon report dates as MMDDYYYY, e.g. "07282026" = 28 Jul 2026.

    These are NOT ISO. `dt.date.fromisoformat("07282026")` reads it as
    year 0728, month 20 and raises "month must be in 1..12" -- which the caller
    used to swallow, leaving settlement_lines permanently empty.
    """
    return dt.datetime.strptime(str(value).strip(), "%m%d%Y").date()


def sync_settlement(supabase, store_id: str, client: WalmartClient, days: int):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).date()

    # Keep both forms: the raw MMDDYYYY string is what the API wants back,
    # the parsed date is what the cutoff compares and what the table stores.
    available = [(raw, _parse_report_date(raw)) for raw in client.get_available_recon_dates()]
    report_dates = [(raw, parsed) for raw, parsed in available if parsed >= cutoff]

    total_rows = 0
    skipped_dates = []
    for api_date, report_date in report_dates:
        rows = []
        for raw in client.get_recon_report(api_date):
            # store_id and report_date are deliberately absent: the RPC stamps
            # both from its own arguments so a row can never land in another
            # store or outside the date being replaced.
            rows.append({
                "customer_order_id": _field(raw, "Customer Order #", "customerOrderId"),
                "order_line_number": _field(raw, "Customer Order line #", "orderLineNumber"),
                "sku": _field(raw, "Partner Item Id", "sku"),
                "transaction_type": _field(raw, "Transaction Type", "transactionType"),
                "amount_type": _field(raw, "Amount Type", "amountType"),
                "amount": _to_number(_field(raw, "Amount", "amount")),
                "quantity": _to_number(_field(raw, "Ship Qty", "quantity")),
                "transaction_posted_at": _parse_timestamp(_field(raw, "Transaction Posted Timestamp", "transactionPostedTimestamp")),
            })

        # Re-syncing a date replaces what's there rather than duplicating it.
        # This used to be a .delete() followed by a separate .insert(): two
        # commits, so a crash or the 300s web timeout between them deleted the
        # date and never refilled it, permanently losing the only real record of
        # Walmart's fees for that day. migration_a23 moved both halves into one
        # transaction. (The old comment here claimed a migration_settlement_fixes.sql
        # had already fixed this. That file never existed.)
        result = supabase.rpc("replace_settlement_date", {
            "p_store_id": store_id,
            "p_report_date": report_date.isoformat(),
            "p_rows": rows,
        }).execute().data
        # PostgREST returns a one-row array for a `returns table` function, but
        # tolerate a bare object or an empty body rather than blowing up on a
        # shape change in the client library.
        outcome = (result[0] if result else None) if isinstance(result, list) else result

        # An empty report over a date that already has rows is refused rather
        # than applied -- far more likely an API hiccup than a genuinely empty
        # settlement day. Surfaced here so it is a visible decision, not silence.
        if outcome and outcome.get("skipped"):
            skipped_dates.append(report_date.isoformat())
            continue

        total_rows += len(rows)

    note = f", kept {len(skipped_dates)} date(s) that came back empty" if skipped_dates else ""
    print(f"  synced {total_rows} settlement rows across {len(report_dates)} report date(s){note}")
    if skipped_dates:
        print(f"  NOT overwritten (empty response): {', '.join(skipped_dates)}")


def main():
    parser = argparse.ArgumentParser(description="Sync Walmart settlement (recon) report for active stores")
    parser.add_argument("--days", type=int, default=7, help="How many days of recon reports to pull (max 90)")
    parser.add_argument(
        "--store-id",
        help="Only sync this store. See sync.py -- the web app always passes this so one owner "
             "can't trigger calls on another owner's Walmart account.",
    )
    args = parser.parse_args()
    days = min(max(args.days, 1), MAX_DAYS)

    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    query = supabase.table("stores").select("store_id, name").eq("active", True)
    if args.store_id:
        query = query.eq("store_id", args.store_id)
    stores = query.execute()

    failures = 0
    for store in stores.data:
        store_id, name = store["store_id"], store["name"]

        creds = supabase.table("store_credentials") \
            .select("client_id, encrypted_client_secret") \
            .eq("store_id", store_id).execute()
        if not creds.data:
            print(f"Skipping {name!r} ({store_id}): no credentials")
            continue

        print(f"Syncing settlement for {name!r} ({store_id})...")

        # One settlement run per store at a time. Two overlapping runs used to be
        # able to interleave their delete and insert halves and erase a report
        # date; A23 made each replace atomic, and this stops them racing at all.
        token = supabase.rpc("acquire_sync_lock", {
            "p_store_id": store_id, "p_feed": "settlement",
            "p_ttl_seconds": LOCK_TTL_SECONDS,
        }).execute().data
        if not token:
            # Not a failure -- the work is already being done. Exiting non-zero
            # here would make a harmless double-click look like a broken sync.
            print(f"  SKIPPED: a settlement sync is already running for {name!r}")
            continue

        started = dt.datetime.now(dt.timezone.utc)
        # Nested so the lease outlives the sync_state write, not just the API
        # work -- releasing earlier would let the next run observe a half-updated
        # state. Same shape as _run_feed in sync.py.
        try:
            try:
                client = WalmartClient(creds.data[0]["client_id"], decrypt(creds.data[0]["encrypted_client_secret"]))
                sync_settlement(supabase, store_id, client, days)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"[:2000]
                print(f"  FAILED: {detail}")
                failures += 1
                supabase.table("sync_state").upsert({
                    "store_id": store_id, "feed": "settlement",
                    "last_run_at": started.isoformat(), "last_status": "failed",
                    "last_error": detail,
                }).execute()
                continue

            supabase.table("sync_state").upsert({
                "store_id": store_id, "feed": "settlement",
                "watermark": started.isoformat(), "last_run_at": started.isoformat(),
                "last_status": "ok", "last_error": None,
            }).execute()
        finally:
            # Runs on the `continue` above too. A hard kill is the only path that
            # skips this, and the lease's TTL covers that.
            supabase.rpc("release_sync_lock", {
                "p_store_id": store_id, "p_feed": "settlement", "p_token": token,
            }).execute()

    if failures:
        # Exit non-zero so web/app.py can trust the return code instead of
        # grepping stdout. This swallowed exception is exactly how the MMDDYYYY
        # date bug kept settlement_lines empty without anyone noticing.
        print(f"{failures} store(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
