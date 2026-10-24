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

__all__ = [
    "list_stores",
    "get_store",
    "create_store",
    "update_store_identity",
    "activation_blockers",
]


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


def create_store(
    conn: psycopg.Connection,
    *,
    name: str,
    walmart_store_key: str,
    timezone: str | None,
    reporting_currency_code: str | None,
    managed_from_year: int | None,
    managed_from_month: int | None,
) -> dict[str, Any]:
    """Insert a new store.

    US-003 AC-02 / BR-12 / period-v1: ``timezone`` and
    ``reporting_currency_code`` are passed through exactly as given, never
    defaulted. ``None`` in means ``NULL`` stored -- there is no
    ``COALESCE``, no literal fallback, and no column default in the schema
    for either. A store created without them is a store that still needs
    configuring before financial reporting activates for it
    (``activation_blockers`` below names exactly that).
    """
    return conn.execute(
        f"""
        insert into stores (
            name, walmart_store_key, timezone, reporting_currency_code,
            managed_from_year, managed_from_month
        )
        values (%s, %s, %s, %s, %s, %s)
        returning {_COLUMNS}
        """,
        (
            name,
            walmart_store_key,
            timezone,
            reporting_currency_code,
            managed_from_year,
            managed_from_month,
        ),
    ).fetchone()


# The only identity columns a caller may change through update_store_identity,
# and the request-field name each is keyed by. An explicit allowlist rather
# than passing caller-supplied keys straight into SQL -- a request field name
# must never become a column name by construction.
_IDENTITY_COLUMNS = {
    "name": "name",
    "timezone": "timezone",
    "reporting_currency_code": "reporting_currency_code",
    "managed_from_year": "managed_from_year",
    "managed_from_month": "managed_from_month",
}


def update_store_identity(
    conn: psycopg.Connection,
    scope: StoreScope,
    store_id: uuid.UUID,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Update one or more identity fields on an existing store.

    US-003 AC-04: only the columns actually present in ``fields`` are
    touched -- a rename that supplies only ``name`` must not disturb
    ``timezone``, ``reporting_currency_code``, ``managed_from_year``,
    ``managed_from_month``, ``is_active`` or ``created_at``.

    F-30 (US-003 audit): ``scope`` is a required argument, matching
    ``get_store`` and ``list_stores`` above -- forgetting it is a
    ``TypeError`` at the call site, not a data leak discovered later -- and
    its filter is composed directly into this statement's ``WHERE``, never
    checked in Python after the fact. The caller (``src.stores.service``)
    still performs its own scoped read first (to answer 404 with the right
    shape and to capture "before" values for the audit event), but this
    statement does not merely trust that earlier read: it is scoped again,
    here, independently, in the one statement that actually writes.

    Raises ``OutOfScope`` if ``store_id`` does not match ``scope`` (or does
    not exist) -- defence in depth alongside the caller's own prior check,
    not a replacement for a 404 response shape the caller still owns.

    Unknown keys in ``fields`` raise ``KeyError`` rather than being silently
    ignored or interpolated -- the allowlist above is the only thing
    standing between a request field name and a SQL column name.

    B-9 (open): whether an inactive store accepts writes at all is not
    settled here either, exactly as ``list_stores`` above notes for reads --
    this function has no ``is_active`` filter of its own.
    """
    set_parts = ["updated_at = now()"]
    params: dict[str, Any] = {"store_id": store_id}
    for key, value in fields.items():
        column = _IDENTITY_COLUMNS[key]  # KeyError on an unrecognised field
        set_parts.append(f"{column} = %({key})s")
        params[key] = value

    where, scope_params = scope.sql_filter("id")
    params.update(scope_params)

    sql = f"""
        update stores
           set {', '.join(set_parts)}
         where id = %(store_id)s and {where}
        returning {_COLUMNS}
    """
    row = conn.execute(sql, params).fetchone()
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
