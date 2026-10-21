"""Scheduled entry point: pulls Walmart orders for every active store and
upserts them into Supabase, tagged by store_id.

Net profit (Walmart referral/WFS fees) requires the settlement report from the
On-Request Reports API — that's a Phase 6 addition and is not built here.

Usage:
    python sync.py --days 30
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

MAX_DAYS = 180

# Re-fetch a window either side of the watermark. Absorbs clock skew between us
# and Walmart, and modifications reported slightly late. Upserts make the
# overlap free.
OVERLAP = dt.timedelta(hours=24)


def _parse_order_date(order: dict) -> str | None:
    raw = order.get("orderDate")
    if raw is None:
        return None
    return dt.datetime.fromtimestamp(raw / 1000, tz=dt.timezone.utc).isoformat()


def _charge_total(order_line: dict, charge_type: str) -> float:
    charges = order_line.get("charges", {}).get("charge", [])
    total = 0.0
    for charge in charges:
        if charge.get("chargeType") == charge_type:
            total += float(charge.get("chargeAmount", {}).get("amount", 0))
    return total


def _product_revenue(order_line: dict) -> float:
    return _charge_total(order_line, "PRODUCT")


def _shipping_revenue(order_line: dict) -> float:
    return _charge_total(order_line, "SHIPPING")


def _order_line_status(order_line: dict) -> str | None:
    statuses = order_line.get("orderLineStatuses", {}).get("orderLineStatus", [])
    return statuses[0].get("status") if statuses else None


def _customer_info(order: dict) -> dict:
    address = order.get("shippingInfo", {}).get("postalAddress", {})
    return {
        "customer_name": address.get("name"),
        "customer_city": address.get("city"),
        "customer_state": address.get("state"),
    }


def _ship_date(order_line: dict):
    """Walmart timestamps the ship event in epoch ms on the line's status."""
    for status in order_line.get("orderLineStatuses", {}).get("orderLineStatus", []) or []:
        raw = (status.get("trackingInfo") or {}).get("shipDateTime")
        if raw:
            return dt.datetime.fromtimestamp(int(raw) / 1000, tz=dt.timezone.utc).isoformat()
    raw = order_line.get("statusDate")
    if raw:
        return dt.datetime.fromtimestamp(int(raw) / 1000, tz=dt.timezone.utc).isoformat()
    return None


def sync_orders(supabase, store_id: str, client: WalmartClient, days: int,
                since: dt.datetime | None = None):
    """Pull orders and upsert them.

    `since` is the incremental watermark. When set, orders are fetched by
    lastModifiedStartDate so STATUS CHANGES come back too -- fetching by
    creation date alone freezes an order at whatever status it had when first
    seen. Without a watermark (first run, or an explicit --days) it falls back
    to a creation-date window.
    """
    order_rows = []
    line_rows = []

    if since:
        orders = client.get_orders(last_modified_start_date=since.date().isoformat())
    else:
        start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).date().isoformat()
        orders = client.get_orders(created_start_date=start)

    for order in orders:
        purchase_order_id = order.get("purchaseOrderId")
        order_rows.append({
            "store_id": store_id,
            "purchase_order_id": purchase_order_id,
            "customer_order_id": order.get("customerOrderId"),
            "order_date": _parse_order_date(order),
            **_customer_info(order),
            "raw": order,
        })

        for line in order.get("orderLines", {}).get("orderLine", []):
            item = line.get("item", {})
            quantity = line.get("orderLineQuantity", {}).get("amount")
            line_rows.append({
                "store_id": store_id,
                "purchase_order_id": purchase_order_id,
                "line_number": line.get("lineNumber"),
                "sku": item.get("sku"),
                "product_name": item.get("productName"),
                "quantity": float(quantity) if quantity is not None else None,
                "product_revenue": _product_revenue(line),
                "shipping_revenue": _shipping_revenue(line),
                "status": _order_line_status(line),
                # Revenue is recognised on shipment, so this has to arrive with
                # the line rather than being backfilled from raw later.
                "shipped_at": _ship_date(line),
            })

    for i in range(0, len(order_rows), 200):
        supabase.table("orders").upsert(order_rows[i:i + 200]).execute()
    for i in range(0, len(line_rows), 200):
        supabase.table("order_lines").upsert(line_rows[i:i + 200]).execute()

    print(f"  synced {len(order_rows)} orders / {len(line_rows)} order lines")
    return len(line_rows)


def sync_products(supabase, store_id: str, client: WalmartClient):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = []
    for item in client.get_items():
        rows.append({
            "store_id": store_id,
            "sku": item.get("sku"),
            "product_name": item.get("productName"),
            "price": item.get("price", {}).get("amount"),
            "published_status": item.get("publishedStatus"),
            "lifecycle_status": item.get("lifecycleStatus"),
            # synced_at has `default now()`, but a default only fires on INSERT.
            # Upserting an existing row left it at first-seen forever, so the
            # column recorded the opposite of what its name promises.
            "synced_at": now,
        })

    for i in range(0, len(rows), 200):
        supabase.table("products").upsert(rows[i:i + 200]).execute()

    print(f"  synced {len(rows)} catalog products")
    return len(rows)


def _watermark(supabase, store_id, feed):
    rows = (supabase.table("sync_state").select("watermark")
            .eq("store_id", store_id).eq("feed", feed).execute().data)
    if not rows or not rows[0]["watermark"]:
        return None
    text = rows[0]["watermark"]
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _run_feed(supabase, store_id, feed, work, *, since):
    """Run one feed, logging it, isolated from the others.

    The watermark advances only on success, so an interruption just means the
    next run redoes that window -- harmless, because every write is an upsert.
    That is what makes the 300-second web timeout safe.
    """
    started = dt.datetime.now(dt.timezone.utc)
    run = supabase.table("sync_runs").insert({
        "store_id": store_id, "feed": feed, "status": "running",
        "started_at": started.isoformat(),
        "window_from": since.isoformat() if since else None,
    }).execute().data[0]

    try:
        written = work()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:2000]
        supabase.table("sync_runs").update({
            "status": "failed", "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "error": detail,
        }).eq("run_id", run["run_id"]).execute()
        supabase.table("sync_state").upsert({
            "store_id": store_id, "feed": feed,
            "last_run_at": started.isoformat(), "last_status": "failed",
            "last_error": detail,
        }).execute()
        print(f"  FAILED [{feed}]: {detail}")
        return False

    supabase.table("sync_runs").update({
        "status": "ok", "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rows_written": written,
    }).eq("run_id", run["run_id"]).execute()
    # Watermark is the moment the run STARTED, minus overlap, so anything
    # modified while it was running is picked up next time rather than missed.
    supabase.table("sync_state").upsert({
        "store_id": store_id, "feed": feed,
        "watermark": (started - OVERLAP).isoformat(),
        "last_run_at": started.isoformat(), "last_status": "ok", "last_error": None,
    }).execute()
    return True


def main():
    parser = argparse.ArgumentParser(description="Sync Walmart orders for active stores")
    parser.add_argument("--days", type=int, default=2,
                        help="Creation-date window for a FULL pull (max 180). Ignored "
                             "once a watermark exists unless --full is passed.")
    parser.add_argument("--full", action="store_true",
                        help="Ignore the watermark and re-pull by creation date. Use for "
                             "backfills.")
    parser.add_argument(
        "--store-id",
        help="Only sync this store. Stores can belong to different owners, so the web app always "
             "passes this -- one owner must never trigger calls on another's Walmart account. "
             "Omit it only for a trusted cron that syncs everything.",
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

        print(f"Syncing {name!r} ({store_id})...")
        try:
            client = WalmartClient(creds.data[0]["client_id"],
                                   decrypt(creds.data[0]["encrypted_client_secret"]))
        except Exception as exc:
            print(f"  FAILED: credentials unusable: {exc}")
            failures += 1
            continue

        since = None if args.full else _watermark(supabase, store_id, "orders")
        print(f"  orders: {'incremental since ' + since.isoformat() if since else f'full, {days}d by creation date'}")

        # Each feed succeeds or fails on its own. Previously one shared
        # try/except meant a products failure reported the whole run as failed
        # even though orders had already been written.
        if not _run_feed(supabase, store_id, "orders",
                         lambda: sync_orders(supabase, store_id, client, days, since=since),
                         since=since):
            failures += 1
        if not _run_feed(supabase, store_id, "products",
                         lambda: sync_products(supabase, store_id, client),
                         since=None):
            failures += 1

    if failures:
        # Exit non-zero so a cron or the web endpoint can actually detect this.
        # The old code printed "FAILED" and exited 0, which is why web/app.py
        # resorts to grepping stdout for the word.
        print(f"{failures} feed(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
