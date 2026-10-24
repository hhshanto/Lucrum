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


def list_stores(conn: psycopg.Connection, scope: StoreScope) -> list[dict[str, Any]]:
    """Stores the caller's scope permits.

    ``is_active`` is not a scope or visibility filter (B-7 / F-21): a store's
    active/inactive state has no bearing on whether it is offered to a caller
    who is otherwise in scope. An inactive store a Manager is assigned to is
    still listed here, exactly like any other in-scope store. ``scope`` is the
    only filter composed into this query -- there is deliberately no
    ``include_inactive`` escape hatch for a future call site to default away.

    Whether an inactive store accepts *writes* is a separate, unsettled
    question and is untouched by this function.
    """
    where, params = scope.sql_filter("id")
    sql = f"select {_COLUMNS} from stores where {where} order by name"
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
