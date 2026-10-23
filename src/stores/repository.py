"""Store reads.

Demonstrates the query convention every later repository follows: ``scope`` is
a required argument, and the scope filter is composed into the SQL rather than
applied afterwards in Python. Filtering after the fetch means the rows were
read before being discarded, which is a leak waiting for a logging statement
or an error message to expose it.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

from src.scoping import OutOfScope, StoreScope

__all__ = ["list_stores", "get_store", "activation_blockers"]


_COLUMNS = """
    id, name, walmart_store_key, timezone, reporting_currency_code,
    managed_from_year, managed_from_month, is_active, created_at, updated_at
"""


def list_stores(
    conn: psycopg.Connection, scope: StoreScope, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    where, params = scope.sql_filter("id")
    sql = f"select {_COLUMNS} from stores where {where}"
    if not include_inactive:
        sql += " and is_active"
    sql += " order by name"
    return conn.execute(sql, params).fetchall()


def get_store(
    conn: psycopg.Connection, scope: StoreScope, store_id: uuid.UUID
) -> dict[str, Any]:
    """Fetch one store.

    Raises OutOfScope for both an unauthorised store and a non-existent one.
    US-002 AC-02: the two must be indistinguishable, or the response confirms
    the existence of stores the caller may not see.
    """
    where, params = scope.sql_filter("id")
    row = conn.execute(
        f"select {_COLUMNS} from stores where id = %(store_id)s and {where}",
        {**params, "store_id": store_id},
    ).fetchone()
    if row is None:
        raise OutOfScope(str(store_id))
    return row


def activation_blockers(store: dict[str, Any]) -> list[str]:
    """Configuration a store still needs before financial reporting.

    US-003 AC-02 requires missing configuration to be *identified* rather than
    guessed. Returning the specific missing keys lets the UI say what to supply
    instead of failing with a generic error, and keeps the absent value absent.
    """
    missing: list[str] = []
    if not store.get("timezone"):
        missing.append("timezone")
    if not store.get("reporting_currency_code"):
        missing.append("reporting_currency_code")
    if store.get("managed_from_year") is None or store.get("managed_from_month") is None:
        missing.append("managed_from")
    return missing
