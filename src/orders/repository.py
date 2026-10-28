"""Reads and writes on ``orders``. Module: Orders.

Follows the convention ``src/stores/repository.py`` and
``src/team/repository.py`` establish: callers pass already-validated values,
never caller-supplied column names, and the one write this module offers is
an explicit, named set of columns rather than a generic ``fields`` dict --
there is exactly one shape of row this module ever writes (the full AC-01
column set), so there is no allowlist to maintain separately from the
function signature itself.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

__all__ = ["upsert_order"]


_COLUMNS = """
    id, store_id, walmart_order_id, walmart_purchase_order_id, sku,
    item_description, order_placed_at_utc, order_date, accounting_month_id,
    scope_status_code, posting_reason_code, posting_status_code, posted_at,
    quantity, selling_amount, currency_code, fulfillment_status_code,
    fulfilled_quantity, acknowledged_at, shipped_at, delivered_at,
    cancelled_at, source_revision, last_imported_at, created_at, updated_at
"""


def upsert_order(
    conn: psycopg.Connection,
    *,
    store_id: uuid.UUID,
    walmart_order_id: str,
    walmart_purchase_order_id: str | None,
    sku: str,
    item_description: str | None,
    order_placed_at_utc: datetime,
    order_date: date,
    scope_status_code: str,
    posting_status_code: str,
    quantity: int,
    selling_amount: Decimal,
    currency_code: str,
    fulfillment_status_code: str,
    last_imported_at: datetime,
) -> tuple[dict[str, Any], bool]:
    """Insert a new order, or update the existing row for this
    ``(store_id, walmart_order_id)`` pair in place. Returns ``(row, created)``.

    AC-02: an ``UPDATE`` over the conflicting row's own identity -- never a
    delete-then-insert. ``id`` and ``created_at`` are never named in the
    ``SET`` clause, so a re-import leaves both unchanged; that is what makes
    the same row survive a refresh instead of being replaced by a fresh one
    (the structural half of "no loss of manual costs" -- once a future child
    table holds a foreign key to this row's ``id``, a delete-then-insert
    would silently orphan or destroy everything attached to it).

    ``scope_status_code`` and ``posting_status_code`` ARE refreshed on
    conflict, alongside ``order_date`` -- both are derived from it
    (``service.py``), and leaving them stale while the date they were
    derived from changes would misrepresent the row.

    ``(xmax = 0)`` is Postgres's own signal that a row was inserted by this
    exact command, which distinguishes a fresh ``INSERT`` from an ``UPDATE``
    taken via the ``ON CONFLICT`` branch without a second round trip or a
    separate existence check racing against this statement.
    """
    row = conn.execute(
        f"""
        insert into orders (
            store_id, walmart_order_id, walmart_purchase_order_id, sku,
            item_description, order_placed_at_utc, order_date,
            scope_status_code, posting_status_code, quantity,
            selling_amount, currency_code, fulfillment_status_code,
            last_imported_at
        )
        values (
            %(store_id)s, %(walmart_order_id)s, %(walmart_purchase_order_id)s,
            %(sku)s, %(item_description)s, %(order_placed_at_utc)s,
            %(order_date)s, %(scope_status_code)s, %(posting_status_code)s,
            %(quantity)s, %(selling_amount)s, %(currency_code)s,
            %(fulfillment_status_code)s, %(last_imported_at)s
        )
        on conflict (store_id, walmart_order_id) do update set
            walmart_purchase_order_id = excluded.walmart_purchase_order_id,
            sku                       = excluded.sku,
            item_description          = excluded.item_description,
            order_placed_at_utc       = excluded.order_placed_at_utc,
            order_date                = excluded.order_date,
            scope_status_code         = excluded.scope_status_code,
            posting_status_code       = excluded.posting_status_code,
            quantity                  = excluded.quantity,
            selling_amount            = excluded.selling_amount,
            currency_code             = excluded.currency_code,
            fulfillment_status_code   = excluded.fulfillment_status_code,
            last_imported_at          = excluded.last_imported_at,
            updated_at                = now()
        returning {_COLUMNS}, (xmax = 0) as created
        """,
        {
            "store_id": store_id,
            "walmart_order_id": walmart_order_id,
            "walmart_purchase_order_id": walmart_purchase_order_id,
            "sku": sku,
            "item_description": item_description,
            "order_placed_at_utc": order_placed_at_utc,
            "order_date": order_date,
            "scope_status_code": scope_status_code,
            "posting_status_code": posting_status_code,
            "quantity": quantity,
            "selling_amount": selling_amount,
            "currency_code": currency_code,
            "fulfillment_status_code": fulfillment_status_code,
            "last_imported_at": last_imported_at,
        },
    ).fetchone()
    created = row.pop("created")
    return row, created
