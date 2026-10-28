"""Reads and writes on ``order_cost_profiles``. Module: Costing.

US-008 -- Enter buying cost per unit. Follows the same discipline
``src/orders/repository.py`` already applies: callers pass already-validated
values, never caller-supplied column names, and store scoping is composed
into the SQL itself rather than checked in Python after a row has already
been fetched (F-22 discipline, mirrored from
``stores_repository.get_store`` and ``orders.repository.get_import_run_state``).

This module is read/write plumbing only. Every piece of ``profit-v1``
arithmetic -- the single half-up rounding, the exact-decimal multiplication,
rejecting a float -- lives in ``src/costing/service.py``, never here
(AGENTS.md: "Business rules live in the service layer").
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import psycopg

from src.scoping import OutOfScope, StoreScope

__all__ = [
    "get_cost_profile",
    "get_order_for_cost_write",
    "save_buying_cost_fields",
]


def _scoped_order_with_profile(
    conn: psycopg.Connection, scope: StoreScope, order_id: uuid.UUID
) -> dict[str, Any] | None:
    """The one query both ``get_cost_profile`` and ``get_order_for_cost_write``
    build on: an order's own identity, joined to its cost profile row if one
    exists (a plain LEFT JOIN -- never a second round trip).

    Scope is composed directly into this query's own ``WHERE`` clause
    (``scope.sql_filter`` against ``orders.store_id``, the column's real
    name -- no CTE/alias indirection is needed here the way
    ``get_import_run_state`` needs one for ``stores.id``). An order whose
    store is outside ``scope`` and an ``order_id`` naming no row at all
    both return ``None`` from this function, and the two public functions
    below both turn that into the identical ``OutOfScope`` -- the two
    cases must be indistinguishable (US-002 AC-02's posture, reused here).

    ``order_cost_profiles.currency_code`` and ``.quantity_snapshot`` are
    both ``NOT NULL`` when a profile row exists (migration 006), so
    ``profile_currency_code is None`` is a reliable, cheap signal for "no
    profile row has ever been written for this order" -- never a separate
    ``EXISTS`` subquery.
    """
    where, scope_params = scope.sql_filter("store_id")
    return conn.execute(
        f"""
        select
            o.id as order_id,
            o.store_id as store_id,
            o.currency_code as order_currency_code,
            o.quantity as order_quantity,
            p.currency_code as profile_currency_code,
            p.quantity_snapshot as profile_quantity_snapshot,
            p.buying_cost_per_unit as buying_cost_per_unit,
            p.buying_cost_per_unit_is_known_zero as buying_cost_per_unit_is_known_zero,
            p.buying_cost_total as buying_cost_total
        from orders o
        left join order_cost_profiles p on p.order_id = o.id
        where o.id = %(order_id)s and ({where})
        """,
        {**scope_params, "order_id": order_id},
    ).fetchone()


def get_cost_profile(
    conn: psycopg.Connection, scope: StoreScope, order_id: uuid.UUID
) -> dict[str, Any]:
    """The current effective buying-cost state for one order. Read-only.

    Returns a fully-populated six-key dict EVEN WHEN no
    ``order_cost_profiles`` row has ever been written for this order --
    the "virtual blank profile" this story's test suite names explicitly:
    ``buying_cost_per_unit=None``, ``buying_cost_per_unit_is_known_zero=
    False``, ``buying_cost_total=None``, with ``currency_code`` and
    ``quantity_snapshot`` read straight from the order itself. This never
    raises merely because nothing has been saved yet -- only the two
    ``OutOfScope`` cases below ever raise.

    Once a profile row DOES exist, ``currency_code`` and
    ``quantity_snapshot`` are read from THAT row, not from the order's
    current values -- ``quantity_snapshot`` is a snapshot precisely so a
    later correction to the order's own ``quantity`` does not retroactively
    change a total that was already computed from a different figure.
    """
    row = _scoped_order_with_profile(conn, scope, order_id)
    if row is None:
        raise OutOfScope(str(order_id))

    has_profile = row["profile_currency_code"] is not None
    return {
        "order_id": row["order_id"],
        "currency_code": (
            row["profile_currency_code"] if has_profile else row["order_currency_code"]
        ),
        "quantity_snapshot": (
            row["profile_quantity_snapshot"] if has_profile else row["order_quantity"]
        ),
        "buying_cost_per_unit": row["buying_cost_per_unit"],
        "buying_cost_per_unit_is_known_zero": (
            row["buying_cost_per_unit_is_known_zero"] if has_profile else False
        ),
        "buying_cost_total": row["buying_cost_total"],
    }


def get_order_for_cost_write(
    conn: psycopg.Connection, scope: StoreScope, order_id: uuid.UUID
) -> dict[str, Any]:
    """Resolve the order's own identity for a write: ``store_id`` (for the
    audit event), ``currency_code`` (DECISIONS.md 2026-10-28 -- always
    inherited from the order, never the store) and the order's current
    ``quantity`` (used to compute a FRESH ``quantity_snapshot`` on this
    write).

    Scope-checked identically to ``get_cost_profile`` above -- same
    ``OutOfScope``, same two indistinguishable cases.
    """
    row = _scoped_order_with_profile(conn, scope, order_id)
    if row is None:
        raise OutOfScope(str(order_id))
    return {
        "order_id": row["order_id"],
        "store_id": row["store_id"],
        "currency_code": row["order_currency_code"],
        "quantity": row["order_quantity"],
    }


def save_buying_cost_fields(
    conn: psycopg.Connection,
    order_id: uuid.UUID,
    *,
    currency_code: str,
    quantity_snapshot: int,
    buying_cost_per_unit: Decimal | None,
    buying_cost_per_unit_is_known_zero: bool,
    buying_cost_total: Decimal | None,
    actor_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Insert or refresh the one ``order_cost_profiles`` row for this order.

    ``ON CONFLICT (order_id) DO UPDATE`` -- never a delete-then-insert, for
    the same reason ``orders.upsert_order`` avoids one: once a future child
    table (``order_cost_field_edits``) holds a foreign key to this row's
    own ``id``, replacing the row would silently orphan it.

    The ``SET`` clause touches ONLY the columns this story owns:
    ``currency_code``, ``quantity_snapshot``, both buying-cost columns,
    ``updated_by``/``updated_at``, and ``record_revision`` (bumped, a plain
    bookkeeping counter -- not an optimistic-concurrency CHECK, which is
    US-011's). It never names ``warehouse_cost_*``, ``third_party_label_
    cost``, ``other_cost``, ``store_settings_version_applied``,
    ``cost_completeness_code`` or ``notes`` -- those belong to later
    stories and a buying-cost correction must never clobber them back to
    NULL on a row another story already populated.

    This function performs no scope check of its own: every caller reaches
    it only after ``get_order_for_cost_write`` has already confirmed the
    order is in scope, using the SAME ``order_id`` -- composing scope
    twice over the identical row would be redundant, not safer.
    """
    row = conn.execute(
        """
        insert into order_cost_profiles (
            order_id, currency_code, quantity_snapshot,
            buying_cost_per_unit, buying_cost_per_unit_is_known_zero,
            buying_cost_total, created_by, updated_by
        )
        values (
            %(order_id)s, %(currency_code)s, %(quantity_snapshot)s,
            %(buying_cost_per_unit)s, %(buying_cost_per_unit_is_known_zero)s,
            %(buying_cost_total)s, %(actor_id)s, %(actor_id)s
        )
        on conflict (order_id) do update set
            currency_code                      = excluded.currency_code,
            quantity_snapshot                  = excluded.quantity_snapshot,
            buying_cost_per_unit                = excluded.buying_cost_per_unit,
            buying_cost_per_unit_is_known_zero  = excluded.buying_cost_per_unit_is_known_zero,
            buying_cost_total                   = excluded.buying_cost_total,
            updated_by                          = excluded.updated_by,
            record_revision                     = order_cost_profiles.record_revision + 1,
            updated_at                          = now()
        returning order_id, currency_code, quantity_snapshot,
                  buying_cost_per_unit, buying_cost_per_unit_is_known_zero,
                  buying_cost_total
        """,
        {
            "order_id": order_id,
            "currency_code": currency_code,
            "quantity_snapshot": quantity_snapshot,
            "buying_cost_per_unit": buying_cost_per_unit,
            "buying_cost_per_unit_is_known_zero": buying_cost_per_unit_is_known_zero,
            "buying_cost_total": buying_cost_total,
            "actor_id": actor_id,
        },
    ).fetchone()
    return row
