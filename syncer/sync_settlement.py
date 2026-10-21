"""Pulls Walmart's recon (settlement) report into settlement_lines.

This is the source of truth for seller-side fees (referral/category fees,
WFS fulfillment/storage fees, refund processing fees, etc) that don't appear
on the Orders API. Run this periodically (e.g. daily) alongside sync.py.

The exact "Amount Type" / "Transaction Type" strings Walmart returns vary by
account, so this script just stores the raw rows. After the first run, check
the settlement_lines table to see what values show up, then populate
fee_category_map so the order_details view can bucket them correctly.

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
    for api_date, report_date in report_dates:
        rows = []
        for raw in client.get_recon_report(api_date):
            rows.append({
                "store_id": store_id,
                "report_date": report_date.isoformat(),
                "customer_order_id": _field(raw, "Customer Order #", "customerOrderId"),
                "order_line_number": _field(raw, "Customer Order line #", "orderLineNumber"),
                "sku": _field(raw, "Partner Item Id", "sku"),
                "transaction_type": _field(raw, "Transaction Type", "transactionType"),
                "amount_type": _field(raw, "Amount Type", "amountType"),
                "amount": _to_number(_field(raw, "Amount", "amount")),
                "quantity": _to_number(_field(raw, "Ship Qty", "quantity")),
                "transaction_posted_at": _parse_timestamp(_field(raw, "Transaction Posted Timestamp", "transactionPostedTimestamp")),
            })

        # Re-syncing a date should replace what's there, not duplicate it.
        # NOTE: this delete-then-insert is not transactional -- an interruption
        # between the two loses the date. Replaced by a content-hash unique index
        # + "on conflict do nothing" in migration_settlement_fixes.sql.
        supabase.table("settlement_lines") \
            .delete() \
            .eq("store_id", store_id) \
            .eq("report_date", report_date.isoformat()) \
            .execute()
        if rows:
            supabase.table("settlement_lines").insert(rows).execute()

        total_rows += len(rows)

    print(f"  synced {total_rows} settlement rows across {len(report_dates)} report date(s)")


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
        started = dt.datetime.now(dt.timezone.utc)
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

    if failures:
        # Exit non-zero so web/app.py can trust the return code instead of
        # grepping stdout. This swallowed exception is exactly how the MMDDYYYY
        # date bug kept settlement_lines empty without anyone noticing.
        print(f"{failures} store(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
