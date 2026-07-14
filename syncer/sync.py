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

from dotenv import load_dotenv
from supabase import create_client

from secrets import decrypt
from walmart_client import WalmartClient

load_dotenv()

MAX_DAYS = 180


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


def sync_orders(supabase, store_id: str, client: WalmartClient, days: int):
    created_start_date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).date().isoformat()

    order_rows = []
    line_rows = []

    for order in client.get_orders(created_start_date):
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
            })

    if order_rows:
        supabase.table("orders").upsert(order_rows).execute()
    if line_rows:
        supabase.table("order_lines").upsert(line_rows).execute()

    print(f"  synced {len(order_rows)} orders / {len(line_rows)} order lines")


def sync_products(supabase, store_id: str, client: WalmartClient):
    rows = []
    for item in client.get_items():
        rows.append({
            "store_id": store_id,
            "sku": item.get("sku"),
            "product_name": item.get("productName"),
            "price": item.get("price", {}).get("amount"),
            "published_status": item.get("publishedStatus"),
            "lifecycle_status": item.get("lifecycleStatus"),
        })

    if rows:
        supabase.table("products").upsert(rows).execute()

    print(f"  synced {len(rows)} catalog products")


def main():
    parser = argparse.ArgumentParser(description="Sync Walmart orders for active stores")
    parser.add_argument("--days", type=int, default=2, help="How many days of orders to pull (max 180)")
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
            client = WalmartClient(creds.data[0]["client_id"], decrypt(creds.data[0]["encrypted_client_secret"]))
            sync_orders(supabase, store_id, client, days)
            sync_products(supabase, store_id, client)
        except Exception as exc:
            print(f"  FAILED: {exc}")


if __name__ == "__main__":
    main()
