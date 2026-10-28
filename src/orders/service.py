"""Order import business rules. Module: Orders.

US-005a -- "Import and refresh orders", AC-01 and AC-02 ONLY
(``walmart-user-stories.md:782-790``, US-005 split three ways: AC-03 is
US-005b, AC-04 through AC-08 are US-005c -- see ``PROGRESS.md``'s 2026-10-27
entry). Nothing here provisions an accounting month, assigns a posting
period, defers a late order, or re-posts one across a protected boundary --
that is ``period-v1`` logic US-005c owns, and ``accounting_month_id`` stays
``NULL`` on every row this module writes, by design (DB-06).

**Capability.** Owner, Admin and Manager may all import, store-scoped
(``walmart-user-stories.md:674``: "Import orders", Yes/Yes/Yes). Unlike
``stores.service`` or ``team.service``, there is no role gate here at all --
the only authorization control is store *scope*
(``stores_repository.get_store``, 404 for an out-of-scope or non-existent
store, never 403 -- US-002 AC-02's posture).

**B-9, interim** (``PROGRESS.md``): whether an inactive store accepts writes
at all is an open Owner question. This module takes the narrowest,
explicitly-interim answer: an inactive store refuses an import, visibly
(``StoreInactive``, never a silent 2xx). If the Owner later rules the other
way, this check is the one line to remove.

**A disclosed design decision, not mandated by any single AC or test in this
story, but required by AGENTS.md's non-negotiable #8** ("Pre-onboarding
orders are... excluded from every total. They are never deferred forward."):
``scope_status_code`` (``managed`` vs ``pre_onboarding``, BR-14) IS computed
here, from ``order_date`` against the store's ``managed_from`` boundary --
not deferred to US-005c. This is a different question from posting-period
assignment: it needs no ``accounting_months`` row, no provisioning, and no
posting decision at all, only two values (``order_date``, already an AC-01
field; ``managed_from_year``/``managed_from_month``, already loaded by the
same ``get_store`` call this module already makes). Defaulting every order
to ``managed`` instead would be factually wrong for a genuinely
pre-onboarding order and would silently include it in a future financial
total -- the exact failure #8 forbids. No test in this story's file exercises
a store with an unconfigured ``managed_from`` or ``timezone`` (every store
``_create_store_db`` creates sets both); the refusal below
(``StoreNotConfiguredForImport``) for that case is written defensively, in
the same "identify missing configuration, never guess" spirit US-003 AC-02
already established, but is untested by this file and should be treated as a
disclosed, reasoned gap rather than a proven one.

``posting_status_code`` follows directly from the above, citing the spec's
own stated defaults rather than inventing new ones: ``out_of_scope`` for a
pre_onboarding order (``database-schema.md:484``, an explicit rule, not a
guess); ``unpostable_no_period`` for a managed order, which is honestly true
for every row this module ever writes, because no ``accounting_months`` row
is ever provisioned in this story
(``database-schema.md:323``: "An order whose date falls outside any
provisioned month for its store is retained with
posting_status_code = unpostable_no_period").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from src import audit, clock
from src.money import Money
from src.orders import repository
from src.scoping import StoreScope
from src.stores import repository as stores_repository

__all__ = [
    "FULFILLMENT_STATUSES",
    "OrderImportItem",
    "StoreInactive",
    "StoreNotConfiguredForImport",
    "import_orders",
]


# database-schema.md:458-464. Exhaustive -- refunded, settlement_pending and
# reconciled belong to the refund-activity and reconciliation dimensions and
# must never reach this set (BR-10, SC-01/SC-12).
FULFILLMENT_STATUSES = frozenset(
    {"acknowledged", "shipped_in_transit", "delivered", "cancelled"}
)

_EVENT_TYPE = "orders_imported"
_ENTITY_TYPE = "order_import"


class StoreInactive(Exception):
    """B-9, interim: an inactive store refuses an import, visibly."""

    def __init__(self, store_id: uuid.UUID) -> None:
        super().__init__(f"store {store_id} is inactive; import refused (B-9, interim)")
        self.store_id = store_id


class StoreNotConfiguredForImport(Exception):
    """The store is missing configuration this import needs to avoid
    guessing: ``timezone`` (to resolve ``order_date``) or ``managed_from``
    (to resolve ``scope_status_code``, BR-14). Named and visible rather than
    a default that could misclassify an order -- the same discipline
    ``clock.InvalidTimezone`` already applies to a missing/invalid timezone,
    extended here to the managed_from boundary.
    """

    def __init__(self, store_id: uuid.UUID, *, missing: list[str]) -> None:
        super().__init__(
            f"store {store_id} is missing configuration required to import "
            f"orders without guessing: {', '.join(missing)}"
        )
        self.store_id = store_id
        self.missing = missing


@dataclass(frozen=True)
class OrderImportItem:
    """One order from an import batch, already shaped (not yet business-
    validated) by the HTTP layer's request model. Carries exactly the eight
    AC-01 fields plus the two nullable extras the full schema also names.
    """

    walmart_order_id: str
    walmart_purchase_order_id: str | None
    sku: str
    item_description: str | None
    order_placed_at_utc: datetime
    quantity: int
    selling_amount: str
    currency_code: str
    fulfillment_status_code: str


def _resolve_order_date(store: dict[str, Any], instant: datetime) -> date:
    """AC-01: order_date always follows the source instant, converted
    through the store's own timezone -- never the UTC calendar date taken
    verbatim (period-v1 / src/clock.py).
    """
    try:
        return clock.to_store_date(instant, store.get("timezone"))
    except clock.InvalidTimezone as exc:
        raise StoreNotConfiguredForImport(store["id"], missing=["timezone"]) from exc


def _resolve_scope_status(store: dict[str, Any], order_date: date) -> str:
    """BR-14: ``managed`` on or after the store's managed_from month,
    ``pre_onboarding`` before it. See this module's docstring for why this
    is computed here rather than deferred to US-005c.
    """
    year = store.get("managed_from_year")
    month = store.get("managed_from_month")
    if year is None or month is None:
        raise StoreNotConfiguredForImport(store["id"], missing=["managed_from"])
    boundary = date(year, month, 1)
    return "managed" if order_date >= boundary else "pre_onboarding"


def _posting_status_for(scope_status_code: str) -> str:
    if scope_status_code == "pre_onboarding":
        # database-schema.md:484, an explicit documented rule.
        return "out_of_scope"
    # database-schema.md:323: true for every row this story ever writes,
    # since no accounting_months row is ever provisioned here.
    return "unpostable_no_period"


def _order_summary(order_id: uuid.UUID, walmart_order_id: str, created: bool) -> dict[str, Any]:
    return {
        "order_id": str(order_id),
        "walmart_order_id": walmart_order_id,
        "created": created,
    }


def _check_import_replay_target(
    existing_event: dict[str, Any], *, store_id: uuid.UUID
) -> None:
    """Confirm a ``DuplicateOperation`` is genuinely a replay of *this*
    import call, not an ``Idempotency-Key`` collision with something else
    (the F-31/F-72 discipline). A batch import has no single ``entity_id``
    to check against (it may touch several orders), so ``store_id`` is the
    only disambiguator available beyond event/entity type -- checked here
    explicitly, the same way ``team.service`` adds its own ``store_id``/
    ``action`` check alongside ``audit.check_replay_target``.
    """
    audit.check_replay_target(
        existing_event, event_type=_EVENT_TYPE, entity_type=_ENTITY_TYPE
    )
    if existing_event["store_id"] != store_id:
        raise audit.ReplayConflict(
            existing_event.get("operation_id", "<unknown>"), existing_event, _EVENT_TYPE
        )


def import_orders(
    conn: psycopg.Connection,
    scope: StoreScope,
    *,
    actor_id: uuid.UUID | None,
    store_id: uuid.UUID,
    orders: list[OrderImportItem],
    operation_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Upsert a batch of orders into one store. Returns ``(results, replayed)``.

    AC-01: every field named by the criterion is persisted exactly as given
    (``repository.upsert_order``); ``selling_amount`` passes through
    ``Money`` so an amount that is not an exact decimal string is rejected
    before it ever reaches a parameter, never silently coerced through a
    float.

    AC-02: keyed by ``(store_id, walmart_order_id)`` -- the same pair that
    carries the real, per-store database uniqueness constraint (DB-12) --
    so a refresh updates the existing row in place rather than creating a
    duplicate, and the same external id reused by a different store creates
    an entirely independent order.

    One audit event per call, not per order: a batch is one write, and
    ``audit_events.operation_id`` is unique, so there is nowhere for a
    second row under the same key to go. ``after_state`` carries every
    order's id and created/updated outcome, which is also what a replay of
    this ``operation_id`` returns instead of re-applying anything.
    """
    store = stores_repository.get_store(conn, scope, store_id)

    if not store["is_active"]:
        raise StoreInactive(store_id)

    # F-55 discipline (US-003 audit), applied here too: resolve
    # operation_id BEFORE attempting any write. An honest retry must be
    # recognised as a replay before any upsert runs a second time.
    existing_event = audit.find_by_operation(conn, operation_id)
    if existing_event is not None:
        _check_import_replay_target(existing_event, store_id=store_id)
        after = existing_event.get("after_state") or {}
        return after.get("orders", []), True

    results: list[dict[str, Any]] = []
    try:
        with conn.transaction():
            for item in orders:
                order_date = _resolve_order_date(store, item.order_placed_at_utc)
                scope_status_code = _resolve_scope_status(store, order_date)
                posting_status_code = _posting_status_for(scope_status_code)

                money = Money(item.selling_amount, item.currency_code).as_settled()

                row, created = repository.upsert_order(
                    conn,
                    store_id=store_id,
                    walmart_order_id=item.walmart_order_id,
                    walmart_purchase_order_id=item.walmart_purchase_order_id,
                    sku=item.sku,
                    item_description=item.item_description,
                    order_placed_at_utc=item.order_placed_at_utc,
                    order_date=order_date,
                    scope_status_code=scope_status_code,
                    posting_status_code=posting_status_code,
                    quantity=item.quantity,
                    selling_amount=money.amount,
                    currency_code=item.currency_code,
                    fulfillment_status_code=item.fulfillment_status_code,
                    last_imported_at=clock.utc_now(),
                )
                results.append(_order_summary(row["id"], row["walmart_order_id"], created))

            audit.record(
                conn,
                audit.AuditEvent(
                    event_type=_EVENT_TYPE,
                    entity_type=_ENTITY_TYPE,
                    entity_id=None,
                    store_id=store_id,
                    operation_id=operation_id,
                    actor_id=actor_id,
                    after_state={"orders": results},
                ),
            )
    except audit.DuplicateOperation as replay:
        # Concurrency backstop: a racer committed between the pre-check
        # above and this call's own writes. Every upsert attempted above
        # was rolled back along with the audit insert that detected the
        # replay (module docstring convention established in
        # stores.service.create_store).
        _check_import_replay_target(replay.existing_event, store_id=store_id)
        after = replay.existing_event.get("after_state") or {}
        return after.get("orders", []), True

    return results, False
