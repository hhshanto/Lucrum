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

-------------------------------------------------------------------------
US-005b-1 -- import run visibility, backend half (AC-03 of US-005, split by
the Owner into a backend half and a screen half, PROGRESS.md 2026-10-28).
Every call to ``import_orders`` now also opens an ``import_batches`` row,
classifies the outcome, records any rejection in ``import_errors``, and
closes the batch -- all inside the SAME transaction as the orders it
writes, so a concurrency-backstop rollback (an operation_id race) discards
the batch bookkeeping along with everything else, exactly as it already
discarded order upserts before this story.

**B-18 (Owner ruling, PROGRESS.md 2026-10-28): a multi-SKU order is
rejected WHOLE.** Two items in one request sharing a ``walmart_order_id``
with different ``sku`` values is detected here in Python, from the
request's own payload -- it needs no database round trip, because BR-02's
violation is visible from the submitted batch alone. Neither line is
written to ``orders``; a single ``import_errors`` row names the order.
Every OTHER, unrelated order in the same batch still commits -- rejection
is scoped to the conflicting order, never to the whole batch (the Owner's
ruling named the order, not the batch).

**B-18.2 (the cross-batch case -- a LATER import whose sku disagrees with
an already-stored row) is explicitly NOT handled here.** That is B-19, an
undecided business question (PROGRESS.md) -- this module still applies the
pre-existing ``sku = excluded.sku`` upsert behaviour for that case,
unguarded, exactly as before this story. Only the WITHIN-one-request-batch
case is detected.

**Four invented conventions, each disclosed at its point of use because
no document defines any of them (B-12's ``store_created`` precedent):**

1. ``import_errors.error_code`` for a BR-02 rejection is
   ``"MULTIPLE_SKUS_FOR_ORDER"`` (see ``_MULTI_SKU_ERROR_CODE`` below) --
   no catalogue of import error codes exists anywhere in the specification.
   A SIBLING rejection -- two lines for one order id sharing the SAME sku,
   a duplicate submission rather than a disagreement -- uses a DISTINCT
   code, ``"DUPLICATE_ORDER_LINE"`` (``_DUPLICATE_LINE_ERROR_CODE``, B-21,
   interim; added in the fix round that closed finding F-96, see
   PROGRESS.md). BR-02's "there are no order line items" clause does not
   turn on whether the SKUs agree, so both shapes are rejected whole and
   visibly -- never one line silently kept and the rest dropped.
2. The HTTP status for "every order in this batch was rejected" is 422
   (``WholeBatchImportRejected``'s handler, ``src/main.py``) -- analogous
   to this file's own existing, equally-unpinned 409/404 choices.
3. **Granularity of ``import_batches`` counts and ``import_errors`` rows is
   this story's own reasoned judgment, not a spec reading**: both are
   counted per distinct ``walmart_order_id`` submitted ("order groups"),
   not per raw request line. A single-item order counts as one row either
   way; the two-line BR-02 conflict case is what makes the distinction
   real -- it is counted and reported as ONE rejected order, not two
   rejected lines, because B-18's ruling itself speaks in terms of orders.
   This choice also makes ``row_count = success_count + error_count`` hold
   as a readable invariant, which per-line counting mixed with per-order
   rejection would not.
4. ``_EVENT_TYPE = "orders_imported"`` is REUSED for a batch in which
   EVERY order was rejected (zero orders actually imported) -- the audit
   event type does not change between a success, a partial batch, and a
   total failure. Defensible (it keeps one idempotency-key namespace
   across every outcome of the same call, rather than splitting replay
   detection across event types for what is still one logical operation)
   but it IS a judgment call, named here rather than left to be noticed
   only by reading the constant.

**G-7's narrow transition rule is applied, read precisely**
(PROGRESS.md: "a batch that raises before any row is written is failed").
Read here as "before any ``orders`` row is written" -- not "before any
write of any kind" -- because AC-03 itself requires the failure to be
DURABLY RECORDED, which means the ``import_batches``/``import_errors``
bookkeeping rows for a total failure are always committed, and only
``orders`` stays untouched. ``WholeBatchImportRejected`` is raised AFTER
the enclosing ``with conn.transaction():`` block has already exited
cleanly (which issues a real ``COMMIT`` at this nesting level -- confirmed
by reading ``psycopg/transaction.py`` directly rather than assumed), so the
batch/error rows survive regardless of what the caller does with the
exception afterward.

A MIXED batch (one order succeeds, another in the same batch is rejected)
is classified ``"partial"`` -- not mandated by any document (no
failed-vs-partial transition rule exists for order-import batches; the
only such rule in the specification governs settlement batches, a
different table under a different policy, per spec-oracle's own G-7
finding). Disclosed here as implementer judgment, not a spec reading.
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
    "WholeBatchImportRejected",
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
_IMPORT_TYPE = "orders"

# Invented, disclosed (module docstring, point 1): no catalogue of import
# error codes exists anywhere in the specification.
_MULTI_SKU_ERROR_CODE = "MULTIPLE_SKUS_FOR_ORDER"

# B-21, interim (new blocker, invariant-auditor, fd34d7a fix round; see
# this module's docstring, "US-005b-1" section, and PROGRESS.md): two
# submitted lines for one walmart_order_id sharing the SAME sku are a
# duplicate LINE submission, not a SKU disagreement -- BR-02's operative
# clause ("There are no order line items") does not mention SKUs at all,
# so this is rejected whole and visibly too, under a DISTINCT error_code
# from the multi-SKU case so an operator reading import_errors can tell
# "more than one line" apart from "more than one SKU". Whether a genuine
# same-SKU duplicate should instead be summed as a quantity split is an
# undecided Owner question (B-21) -- this is the interim, reversible
# ruling, not a confirmed rule.
_DUPLICATE_LINE_ERROR_CODE = "DUPLICATE_ORDER_LINE"


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


class WholeBatchImportRejected(Exception):
    """Every order submitted in this import batch was rejected (B-18): no
    row was written to ``orders``. Raised AFTER the ``import_batches``/
    ``import_errors``/audit bookkeeping for this run has already committed
    -- see this module's own docstring for why that ordering is correct,
    not accidental. ``errors`` carries one dict per rejected order
    (``walmart_order_id``, ``error_code``, ``message``), the same shape
    ``main.py``'s handler surfaces in the response body.
    """

    def __init__(
        self,
        *,
        store_id: uuid.UUID,
        batch_id: uuid.UUID,
        errors: list[dict[str, Any]],
    ) -> None:
        super().__init__(
            f"every order in import batch {batch_id} for store {store_id} "
            "was rejected; nothing was written to orders"
        )
        self.store_id = store_id
        self.batch_id = batch_id
        self.errors = errors


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


def _group_by_walmart_order_id(
    orders: list[OrderImportItem],
) -> dict[str, list[OrderImportItem]]:
    """Group submitted lines by ``walmart_order_id``, preserving first-seen
    order (plain ``dict`` insertion order, Python 3.7+). Grouping alone
    does not decide anything -- the caller inspects each group's size and
    distinct ``sku`` count to tell apart a single line, a multi-SKU
    conflict (B-18), and a same-SKU duplicate-line submission (B-21,
    interim) -- both of the latter two are rejected WHOLE, visibly, before
    any database write is attempted; neither silently keeps ``items[0]``
    and drops the rest (the live defect F-96 found and this fix closes).
    """
    groups: dict[str, list[OrderImportItem]] = {}
    for item in orders:
        groups.setdefault(item.walmart_order_id, []).append(item)
    return groups


def _multi_sku_error(walmart_order_id: str, skus: list[str]) -> dict[str, Any]:
    message = (
        f"order {walmart_order_id!r} carries {len(skus)} distinct SKUs "
        f"({', '.join(skus)}) within one import batch; BR-02 permits "
        "exactly one SKU per order. Rejected whole -- neither line was "
        "persisted (B-18, Owner ruling, PROGRESS.md 2026-10-28)."
    )
    return {
        "walmart_order_id": walmart_order_id,
        "error_code": _MULTI_SKU_ERROR_CODE,
        "message": message,
    }


def _duplicate_line_error(walmart_order_id: str, line_count: int, sku: str) -> dict[str, Any]:
    """B-21, interim (see ``_DUPLICATE_LINE_ERROR_CODE`` above): two or
    more lines in one batch share BOTH ``walmart_order_id`` and ``sku`` --
    a duplicate LINE submission, not a SKU disagreement. Rejected whole,
    visibly, same as the multi-SKU case, under a distinct error_code.
    """
    message = (
        f"order {walmart_order_id!r} was submitted {line_count} times "
        f"within one import batch, all sharing sku {sku!r}; BR-02's "
        "'no order line items' clause treats this as a multi-line order "
        "regardless of SKU agreement. Rejected whole -- no line was "
        "persisted (B-21, interim ruling, PROGRESS.md)."
    )
    return {
        "walmart_order_id": walmart_order_id,
        "error_code": _DUPLICATE_LINE_ERROR_CODE,
        "message": message,
    }


def _replay_import_outcome(
    after_state: dict[str, Any], *, store_id: uuid.UUID
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Reconstruct the original call's outcome from a replayed audit event
    (F-88 applied to this story's own write surface: a replayed FAILURE
    must report the SAME failure, not silently succeed).
    """
    if after_state.get("outcome") == "failed":
        # F-101: `after_state["batch_id"]` was stored as `str(batch["id"])`
        # (jsonb has no UUID type) -- converted back to `uuid.UUID` here so
        # `WholeBatchImportRejected.batch_id` is the SAME type on both the
        # replay path and the original-raise path (this module's own
        # `outcome == "failed"` branch, below, in `import_orders` itself),
        # never a `str` on one and a `uuid.UUID` on the other.
        raise WholeBatchImportRejected(
            store_id=store_id,
            batch_id=uuid.UUID(after_state["batch_id"]),
            errors=after_state.get("errors", []),
        )
    return after_state.get("orders", []), after_state.get("errors", []), True


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Upsert a batch of orders into one store.

    Returns ``(results, errors, replayed)``. ``errors`` is one dict per
    rejected order -- the B-18 multi-SKU shape or the B-21 same-SKU
    duplicate-line shape, told apart by ``error_code`` -- empty when every
    order in the batch was accepted. Raises
    ``WholeBatchImportRejected`` instead of returning when EVERY order in
    the batch was rejected (see this module's own docstring for why that is
    the chosen total-failure boundary).

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
    accepted order's id/outcome, every rejected order's error, the
    import_batches row id, and the overall outcome label -- everything a
    replay needs to reconstruct the ORIGINAL response (including a FAILED
    one, via ``_replay_import_outcome``) rather than merely the orders.
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
        return _replay_import_outcome(after, store_id=store_id)

    groups = _group_by_walmart_order_id(orders)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        with conn.transaction():
            batch = repository.create_import_batch(
                conn,
                import_type=_IMPORT_TYPE,
                store_id=store_id,
                source_name=None,
                status_code="processing",
                imported_by=actor_id,
                started_at=clock.utc_now(),
            )

            error_rows: list[dict[str, Any]] = []
            for walmart_order_id, items in groups.items():
                skus = sorted({item.sku for item in items})
                if len(skus) > 1:
                    # B-18: reject the whole order, visibly. Detected from
                    # the submitted payload alone -- no database round trip
                    # needed to know two lines in THIS batch disagree.
                    errors.append(_multi_sku_error(walmart_order_id, skus))
                    error_rows.append(
                        {
                            "external_reference": walmart_order_id,
                            "raw_row": {
                                "walmart_order_id": walmart_order_id,
                                "skus": skus,
                            },
                        }
                    )
                    continue

                if len(items) > 1:
                    # B-21, interim: more than one line for this order id,
                    # all sharing the SAME sku -- a duplicate LINE
                    # submission, not a SKU disagreement. BR-02's "there
                    # are no order line items" clause rejects this whole
                    # too, visibly, under a distinct error_code (see
                    # ``_DUPLICATE_LINE_ERROR_CODE`` above). Never
                    # ``items[0]`` silently taken with the rest dropped.
                    errors.append(
                        _duplicate_line_error(walmart_order_id, len(items), skus[0])
                    )
                    error_rows.append(
                        {
                            "external_reference": walmart_order_id,
                            "raw_row": {
                                "walmart_order_id": walmart_order_id,
                                "sku": skus[0],
                                "line_count": len(items),
                            },
                        }
                    )
                    continue

                item = items[0]
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

            # Granularity: one count/row per distinct walmart_order_id
            # submitted ("order group"), not per raw request line -- this
            # module's own docstring, point 3.
            row_count = len(groups)
            success_count = len(results)
            error_count = len(errors)

            if success_count == 0 and error_count > 0:
                outcome = "failed"
            elif error_count == 0:
                outcome = "completed"
            else:
                # Disclosed implementer judgment (module docstring): no
                # failed-vs-partial transition rule exists for order-import
                # batches anywhere in the specification.
                outcome = "partial"

            repository.finish_import_batch(
                conn,
                batch_id=batch["id"],
                status_code=outcome,
                finished_at=clock.utc_now(),
                row_count=row_count,
                success_count=success_count,
                error_count=error_count,
            )

            for error, error_row in zip(errors, error_rows):
                repository.insert_import_error(
                    conn,
                    import_batch_id=batch["id"],
                    row_number=None,
                    external_reference=error_row["external_reference"],
                    error_code=error["error_code"],
                    error_message=error["message"],
                    raw_row=error_row["raw_row"],
                )

            audit.record(
                conn,
                audit.AuditEvent(
                    event_type=_EVENT_TYPE,
                    entity_type=_ENTITY_TYPE,
                    entity_id=None,
                    store_id=store_id,
                    operation_id=operation_id,
                    actor_id=actor_id,
                    after_state={
                        "orders": results,
                        "errors": errors,
                        "batch_id": str(batch["id"]),
                        "outcome": outcome,
                    },
                ),
            )
    except audit.DuplicateOperation as replay:
        # Concurrency backstop: a racer committed between the pre-check
        # above and this call's own writes. Everything attempted above --
        # the batch row, every upsert, every error row -- was rolled back
        # along with the audit insert that detected the replay (module
        # docstring convention established in stores.service.create_store).
        _check_import_replay_target(replay.existing_event, store_id=store_id)
        after = replay.existing_event.get("after_state") or {}
        return _replay_import_outcome(after, store_id=store_id)

    if outcome == "failed":
        # Force a real COMMIT here, explicitly -- confirmed empirically,
        # not merely read in docs, that this is required: `stores_
        # repository.get_store` above already ran a SELECT on this same
        # `conn`, which (autocommit=False, the project default) opens an
        # AMBIENT transaction before `with conn.transaction():` is ever
        # entered. That makes the `with conn.transaction():` block above a
        # NESTED savepoint, not the outermost transaction -- its clean exit
        # only RELEASEs the savepoint, it does not COMMIT. Without this
        # call, raising below propagates out through `with connection() as
        # conn:` (`src/db/connection.py`'s own wrapper around
        # `psycopg_pool.ConnectionPool.connection()`, used by
        # `src/main.py`) -- it is THAT pool context manager's `__exit__`,
        # not `psycopg.Connection.__exit__` directly, that calls
        # `conn.rollback()` on exception, rolling back the whole ambient
        # transaction and undoing the batch and error rows this function
        # just told the caller were durably recorded. `conn.commit()` here
        # ends that ambient transaction for real, before the exception is
        # raised.
        conn.commit()
        raise WholeBatchImportRejected(
            store_id=store_id, batch_id=batch["id"], errors=errors
        )

    return results, errors, False
