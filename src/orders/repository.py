"""Reads and writes on ``orders``, ``import_batches`` and ``import_errors``.
Module: Orders.

Follows the convention ``src/stores/repository.py`` and
``src/team/repository.py`` establish: callers pass already-validated values,
never caller-supplied column names, and the one write this module offers per
table is an explicit, named set of columns rather than a generic ``fields``
dict -- there is exactly one shape of row this module ever writes per table,
so there is no allowlist to maintain separately from the function signature
itself.

US-005b-1 adds the import-run bookkeeping tables (``import_batches``,
``import_errors``, ``database-schema.md:965-998``) and the one new read,
``get_import_run_state``. ``get_import_run_state``'s own scope check follows
``stores_repository.get_store``'s discipline exactly (F-22, US-002a audit):
the scope predicate is composed into the SQL sent to the database, never
checked in Python after a row has already been fetched.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from src.scoping import OutOfScope, StoreScope

__all__ = [
    "upsert_order",
    "create_import_batch",
    "finish_import_batch",
    "insert_import_error",
    "get_import_run_state",
    "get_order_level_freshness",
]


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


_BATCH_COLUMNS = """
    id, import_type, store_id, source_name, status_code, imported_by,
    started_at, finished_at, row_count, success_count, error_count
"""


def create_import_batch(
    conn: psycopg.Connection,
    *,
    import_type: str,
    store_id: uuid.UUID | None,
    source_name: str | None,
    status_code: str,
    imported_by: uuid.UUID | None,
    started_at: datetime,
) -> dict[str, Any]:
    """Open a new import run. ``row_count``/``success_count``/``error_count``
    are left at their database default (0) -- they are not yet known, and
    this is a bookkeeping counter, not a financial figure, so a 0 default is
    correct here (unlike a cost or a recovered amount -- ``test_import_
    batches_counts_default_to_zero``'s own docstring draws this distinction).
    """
    return conn.execute(
        f"""
        insert into import_batches (
            import_type, store_id, source_name, status_code, imported_by, started_at
        )
        values (
            %(import_type)s, %(store_id)s, %(source_name)s, %(status_code)s,
            %(imported_by)s, %(started_at)s
        )
        returning {_BATCH_COLUMNS}
        """,
        {
            "import_type": import_type,
            "store_id": store_id,
            "source_name": source_name,
            "status_code": status_code,
            "imported_by": imported_by,
            "started_at": started_at,
        },
    ).fetchone()


def finish_import_batch(
    conn: psycopg.Connection,
    *,
    batch_id: uuid.UUID,
    status_code: str,
    finished_at: datetime,
    row_count: int,
    success_count: int,
    error_count: int,
) -> dict[str, Any]:
    """Record the terminal outcome of an import run.

    ``status_code`` here is one of ``completed``/``failed``/``partial`` --
    never ``pending``/``processing`` again, since this is always the LAST
    write made to a given batch row in this module (no code path reopens a
    finished batch).
    """
    return conn.execute(
        f"""
        update import_batches
           set status_code   = %(status_code)s,
               finished_at   = %(finished_at)s,
               row_count     = %(row_count)s,
               success_count = %(success_count)s,
               error_count   = %(error_count)s
         where id = %(batch_id)s
        returning {_BATCH_COLUMNS}
        """,
        {
            "batch_id": batch_id,
            "status_code": status_code,
            "finished_at": finished_at,
            "row_count": row_count,
            "success_count": success_count,
            "error_count": error_count,
        },
    ).fetchone()


def _jsonb(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    # default=str so anything not natively JSON-serialisable (a date, a
    # UUID) survives as its exact string form -- the same discipline
    # audit.py's own _json() already applies to before_state/after_state.
    return json.dumps(value, default=str, sort_keys=True)


def insert_import_error(
    conn: psycopg.Connection,
    *,
    import_batch_id: uuid.UUID,
    row_number: int | None,
    external_reference: str | None,
    error_code: str,
    error_message: str,
    raw_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Record one reviewable import error.

    This story's own chosen granularity (disclosed in ``service.py``): one
    row per REJECTED ORDER, not per submitted line -- so a within-batch
    multi-SKU conflict (two lines, one ``walmart_order_id``) produces a
    single ``import_errors`` row naming the order, not one row per line.
    """
    return conn.execute(
        """
        insert into import_errors (
            import_batch_id, row_number, external_reference, error_code,
            error_message, raw_row
        )
        values (
            %(import_batch_id)s, %(row_number)s, %(external_reference)s,
            %(error_code)s, %(error_message)s, %(raw_row)s
        )
        returning id, import_batch_id, row_number, external_reference,
                  error_code, error_message, raw_row, created_at
        """,
        {
            "import_batch_id": import_batch_id,
            "row_number": row_number,
            "external_reference": external_reference,
            "error_code": error_code,
            "error_message": error_message,
            "raw_row": _jsonb(raw_row),
        },
    ).fetchone()


def get_import_run_state(
    conn: psycopg.Connection,
    scope: StoreScope,
    store_id: uuid.UUID,
    *,
    import_type: str = "orders",
) -> dict[str, Any]:
    """The queryable import state for one store (US-005b-1, AC-03.3/AC-03.4).

    Returns exactly four keys -- ``never_imported`` (bool),
    ``last_success_at`` (datetime | None), ``last_attempt_status``
    (str | None), ``last_attempt_finished_at`` (datetime | None). No
    "is_current"/"is_fresh" field is ever added: AC-03's "stale data is not
    labeled current" clause is enforced structurally by this function never
    offering such a field, not merely by a convention at the call site.

    No staleness THRESHOLD is computed or stored here, anywhere -- see
    ``service.py``'s module docstring and PROGRESS.md: no ``period-v1``
    clause, no policy table, no config row for one exists. The negative
    guarantee (never claims to be current after a failure) comes from
    reporting ``last_attempt_status`` honestly, never falling back to the
    last SUCCESSFUL status when the most recent attempt did not succeed.

    **Read authority is INTERIM, unconfirmed (new blocker B-20, PROGRESS.md
    -- labelled here exactly as B-13/B-15 were).** No capability-matrix row
    distinguishes "view import history" from "trigger an import". This
    function applies store-scope-only (the same gate as triggering an
    import), not a role gate, on the reasoning that all three roles
    (Owner/Admin/Manager) may already trigger an import
    (``walmart-user-stories.md:674``) -- a Manager who could trigger an
    import but not see whether it failed would defeat AC-03's purpose. This
    is NOT the B-13/B-15 Owner-only shape: those concerned power over other
    users; this is operational data about a store the caller already has
    scope to.

    Scope is checked by composing ``scope.sql_filter`` directly into a
    query that touches ``stores`` (F-22 discipline, mirrored from
    ``stores_repository.get_store``) -- never by fetching a row and asking
    ``scope.permits(...)`` afterwards. The scope filter is requested against
    the column name ``"store_id"`` rather than ``"id"`` so the same fragment
    composes uniformly with every store-scoped table this module's own
    queries touch (``import_batches``); ``stores.id`` is aliased to
    ``store_id`` inside a CTE so the fragment can be applied in the CTE's
    own outer ``WHERE`` -- a Postgres column alias cannot be referenced in
    the ``WHERE`` of the SAME ``SELECT`` that defines it, which is why this
    is one CTE and not a single flat query.
    """
    where, scope_params = scope.sql_filter("store_id")
    row = conn.execute(
        f"""
        with target_store as (
            select id as store_id from stores where id = %(target_store_id)s
        )
        select store_id from target_store where {where}
        """,
        {**scope_params, "target_store_id": store_id},
    ).fetchone()
    if row is None:
        raise OutOfScope(str(store_id))

    batches = conn.execute(
        """
        select status_code, finished_at
          from import_batches
         where store_id = %(store_id)s and import_type = %(import_type)s
         order by started_at desc
        """,
        {"store_id": store_id, "import_type": import_type},
    ).fetchall()

    if not batches:
        # Unknown is not zero (AGENTS.md #4): "never imported" is a
        # DISTINCT fact from "imported and every attempt so far failed" --
        # both would otherwise report last_success_at is None, which is why
        # this flag exists as its own field rather than being inferred.
        return {
            "never_imported": True,
            "last_success_at": None,
            "last_attempt_status": None,
            "last_attempt_finished_at": None,
        }

    last_attempt = batches[0]
    last_success = next((b for b in batches if b["status_code"] == "completed"), None)
    return {
        "never_imported": False,
        "last_success_at": last_success["finished_at"] if last_success else None,
        "last_attempt_status": last_attempt["status_code"],
        "last_attempt_finished_at": last_attempt["finished_at"],
    }


def get_order_level_freshness(
    conn: psycopg.Connection,
    scope: StoreScope,
    store_id: uuid.UUID,
) -> datetime | None:
    """The most recent ``orders.last_imported_at`` instant for one store
    (US-005b-2 F-100; B-22 closed "both", ``DECISIONS.md`` 2026-10-28).

    Deliberately distinct from ``get_import_run_state``'s run-level
    ``last_success_at`` above, and reads a DIFFERENT column of a DIFFERENT
    table: this is the per-ORDER stamp ``upsert_order`` writes on every row
    that actually lands, success or not, for every import attempt that
    accepts it -- never ``import_batches``. A ``partial`` batch (B-19/B-21's
    designed outcome: some rows accepted, one whole order rejected) still
    advances the accepted rows' own ``last_imported_at``, even though only a
    ``completed`` batch would ever advance ``get_import_run_state``'s
    ``last_success_at``. Callers must not alias one for the other -- they
    answer different questions and are expected to diverge.

    Returns ``None`` when the store has never had a single ``orders`` row
    written. This is UNKNOWN, never zero/epoch (AGENTS.md #4: "unknown is
    not zero") -- there is no ``coalesce(..., some_default)`` here, by
    design; an empty store renders no element for this fact at all, rather
    than a fabricated instant.

    Scope discipline is identical to ``get_import_run_state`` immediately
    above, copied rather than factored out so this function's own ``WHERE``
    stays self-contained: the predicate is composed into the SQL itself
    (``stores`` + ``scope.sql_filter`` in a CTE), never checked in Python
    after a row has already been fetched, so an unassigned store and a
    nonexistent one raise the identical ``OutOfScope`` here too (F-22
    discipline).
    """
    where, scope_params = scope.sql_filter("store_id")
    scope_row = conn.execute(
        f"""
        with target_store as (
            select id as store_id from stores where id = %(target_store_id)s
        )
        select store_id from target_store where {where}
        """,
        {**scope_params, "target_store_id": store_id},
    ).fetchone()
    if scope_row is None:
        raise OutOfScope(str(store_id))

    row = conn.execute(
        """
        select max(last_imported_at) as last_imported_at
          from orders
         where store_id = %(store_id)s
        """,
        {"store_id": store_id},
    ).fetchone()
    return row["last_imported_at"]
