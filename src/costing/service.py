"""Costing business rules: buying cost per unit. Module: Costing.

US-008 -- Enter buying cost per unit (AC-01..AC-04). The first money code in
the system (`PROGRESS.md`), so every pattern set here is the one later
costing stories (US-009/US-010/US-011) are expected to follow rather than
reinvent.

``profit-v1`` arithmetic (`walmart-user-stories.md:325-344`) lives here and
nowhere else: ``src/money.Money`` supplies the one rounding implementation
(``quantize``/``as_settled``, half-up, applied once); this module never
reimplements decimal handling a second time (AGENTS.md #5/#6). Two
INDEPENDENT single roundings happen per save -- not a double rounding of the
same number:

  1. the manually entered per-unit value is rounded to 2dp for its own
     storage/display (``as_settled()`` on the exact entered amount);
  2. the total is rounded to 2dp from the EXACT, UNROUNDED entered value
     times quantity (``as_settled()`` on the exact product) -- never from
     the already-rounded per-unit value above.

Unknown vs. known-zero (US-008 AC-02/AC-03, `database-schema.md:575`):
``buying_cost_per_unit=None`` means blank/unknown and is never coerced to
zero; an explicitly entered ``"0.00"`` is a KNOWN value, flagged
``buying_cost_per_unit_is_known_zero=True``, and produces a KNOWN
``buying_cost_total`` of zero -- not ``None``. No SKU or store default is
ever substituted for a missing cost (AC-02): every read and write here is
keyed strictly by ``order_id``, never by ``(store_id, sku)`` -- the obvious
shortcut BR-02's "one SKU per order" framing invites, and exactly wrong
(AC-04).

Capability (`walmart-user-stories.md:677`): Owner, Admin AND Manager may
"enter and correct manual costs in an open period" -- all three of this
system's roles (`access-v1`; ``users_role_check`` permits exactly
``owner``/``admin``/``manager``, `002_access.sql`). There is therefore no
role this action could be Forbidden to, and no fourth role exists to widen
the matrix against later -- so this module adds no role gate and
``save_buying_cost`` takes no ``role`` parameter. Store SCOPE is still
fully enforced (an actor's own assignments), via ``StoreScope`` composed
into the repository's SQL exactly as every other scoped write in this
codebase does it; only the role-capability axis is a no-op here because
the matrix itself grants it to everyone who could call this function at
all.

BR-08 (closed-month protection) is explicitly OUT OF SCOPE for this story
(`PROGRESS.md`): ``accounting_month_id`` is nullable and nothing posts to a
period yet, so there is no period to protect against and no
``has_ever_been_closed`` check is added here. A later story adds it; this
one must not fabricate it ahead of the table that would drive it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import psycopg

from src import audit
from src.costing import repository
from src.money import Money
from src.scoping import StoreScope

__all__ = ["save_buying_cost", "get_cost_completeness"]


# B-12-style naming judgement (store_created precedent, src/stores/service.py):
# api-service-design.md:1186-1209 names no audit event type for a cost-profile
# write at all -- only `cost_edit_policy_updated` (a DIFFERENT thing: the
# POLICY, not an order's own cost). This follows the same convention every
# other event name in that table already uses (`<entity>_<change>`).
_EVENT_TYPE = "order_cost_profile_saved"
_ENTITY_TYPE = "order"

# AC-02's own name for the one component this story populates
# (`database-schema.md:251`, restated in the test file's own module
# docstring) -- not invented from nothing.
_BUYING_COST_COMPONENT_CODE = "buying_cost_total"


def save_buying_cost(
    conn: psycopg.Connection,
    scope: StoreScope,
    *,
    actor_id: uuid.UUID | None,
    order_id: uuid.UUID,
    buying_cost_per_unit: str | None,
    operation_id: str,
) -> dict[str, Any]:
    """Enter (or clear) the buying cost per unit for one order. Returns a
    six-key dict: ``order_id``, ``currency_code``, ``quantity_snapshot``,
    ``buying_cost_per_unit`` (``Decimal | None``),
    ``buying_cost_per_unit_is_known_zero`` (``bool``), ``buying_cost_total``
    (``Decimal | None``).

    ``buying_cost_per_unit`` is a decimal-exact ``str`` (e.g. ``"0.00"``,
    ``"1.005"``) or ``None`` for an explicit blank/clear. A ``float`` is
    rejected with ``InvalidMoney`` -- the same exception ``src.money.Money``
    already raises for one, reused here rather than reimplemented (AGENTS.md
    #5/#6).

    Raises ``src.scoping.OutOfScope`` -- never a different exception, never
    403-shaped -- for an order whose store is outside ``scope`` AND for an
    ``order_id`` that does not exist at all. Resolved FIRST, before any
    ``operation_id`` replay logic: an out-of-scope request must never
    succeed merely by replaying someone else's ``operation_id``.
    """
    # Scope check up front, outside any write. Composed into the query
    # layer by `repository.get_order_for_cost_write` (F-22 discipline) --
    # never a fetch-then-check in Python. Also supplies the order's own
    # `currency_code` (DECISIONS.md 2026-10-28: inherited from the order,
    # never the store) and current `quantity`, used to compute a FRESH
    # `quantity_snapshot` for THIS write.
    order = repository.get_order_for_cost_write(conn, scope, order_id)

    # F-55 precedent (src/stores/service.py): resolve operation_id BEFORE
    # computing anything from the new payload. An honest retry resends the
    # identical value; the replay guarantee is "apply nothing new", so the
    # new value must never even be validated, let alone written, once a
    # prior outcome for this exact operation_id is found.
    existing_event = audit.find_by_operation(conn, operation_id)
    if existing_event is not None:
        audit.check_replay_target(
            existing_event, event_type=_EVENT_TYPE, entity_type=_ENTITY_TYPE, entity_id=order_id
        )
        return repository.get_cost_profile(conn, scope, order_id)

    currency_code = order["currency_code"]
    quantity_snapshot = order["quantity"]

    per_unit_value, is_known_zero, total_value = _compute_buying_cost(
        buying_cost_per_unit, currency_code, quantity_snapshot
    )

    try:
        with conn.transaction():
            saved = repository.save_buying_cost_fields(
                conn,
                order_id,
                currency_code=currency_code,
                quantity_snapshot=quantity_snapshot,
                buying_cost_per_unit=per_unit_value,
                buying_cost_per_unit_is_known_zero=is_known_zero,
                buying_cost_total=total_value,
                actor_id=actor_id,
            )
            audit.record(
                conn,
                audit.AuditEvent(
                    event_type=_EVENT_TYPE,
                    entity_type=_ENTITY_TYPE,
                    entity_id=order_id,
                    store_id=order["store_id"],
                    operation_id=operation_id,
                    actor_id=actor_id,
                    after_state={
                        "buying_cost_per_unit": per_unit_value,
                        "buying_cost_per_unit_is_known_zero": is_known_zero,
                        "buying_cost_total": total_value,
                        "quantity_snapshot": quantity_snapshot,
                        "currency_code": currency_code,
                    },
                ),
            )
    except audit.DuplicateOperation as replay:
        # Concurrency backstop, same shape as src/stores/service.py's own
        # (F-63): a racer committed between the pre-check above and this
        # call's own insert. The write attempted above was rolled back
        # along with the audit insert that detected the replay -- nothing
        # from this call's payload was persisted.
        audit.check_replay_target(
            replay.existing_event,
            event_type=_EVENT_TYPE,
            entity_type=_ENTITY_TYPE,
            entity_id=order_id,
        )
        return repository.get_cost_profile(conn, scope, order_id)

    return {
        "order_id": saved["order_id"],
        "currency_code": saved["currency_code"],
        "quantity_snapshot": saved["quantity_snapshot"],
        "buying_cost_per_unit": saved["buying_cost_per_unit"],
        "buying_cost_per_unit_is_known_zero": saved["buying_cost_per_unit_is_known_zero"],
        "buying_cost_total": saved["buying_cost_total"],
    }


def _compute_buying_cost(
    buying_cost_per_unit: str | None, currency_code: str, quantity_snapshot: int
) -> tuple[Decimal | None, bool, Decimal | None]:
    """The one place this module touches ``profit-v1`` arithmetic.

    A blank input (``None``) stays unknown on every axis: no per-unit
    value, not a known zero, no total -- never coerced to zero for
    calculation (AGENTS.md #4).

    A non-blank input is validated and rounded through ``Money`` only --
    never a second decimal implementation. ``Money(...)`` itself rejects a
    ``float`` (``InvalidMoney``), rejects anything that is not an exact
    decimal, and ``quantity_snapshot`` is this order's own imported quantity
    (never another order's, never a default) so the total it produces can
    never silently combine two orders' figures (AC-01/AC-04).

    The total is computed from the EXACT entered value (before rounding
    that value for its own storage) times quantity, with a SINGLE half-up
    rounding applied to that product -- never to an already-rounded
    per-unit value (profit-v1: "No intermediate rounding... A single
    half-up rounding is applied once, to the final result").
    """
    if buying_cost_per_unit is None:
        return None, False, None

    exact = Money(buying_cost_per_unit, currency_code)
    per_unit_rounded = exact.as_settled()
    total_rounded = (exact * quantity_snapshot).as_settled()

    return per_unit_rounded.amount, per_unit_rounded.amount == 0, total_rounded.amount


def get_cost_completeness(
    conn: psycopg.Connection, scope: StoreScope, order_id: uuid.UUID
) -> dict[str, Any]:
    """Whether this order's buying cost is known, for AC-02/AC-03's own
    completeness question. Returns at least ``complete`` (bool) and
    ``missing_component_codes`` (list[str]).

    Scoped to EXACTLY the one component this story populates --
    ``"buying_cost_total"`` (`database-schema.md:251`). ``profit-v1`` names
    five further cost components (`order_estimate_versions`,
    ``warehouse_cost_effective``, Walmart deductions, etc.), but none of
    those tables or columns exist yet (US-009/US-010 build them) --
    reporting them as "missing" here would not be a derived fact, it would
    be a fabricated placeholder with nothing behind it (AGENTS.md: "never
    invent a rule"). ``complete`` is therefore only ever true when this
    story's own component is known; it is intentionally not yet a claim
    about overall profit-completeness across all six components, and a
    later story (US-009/US-010) is expected to widen
    ``missing_component_codes`` as each further component gains a real
    column to read -- never by this function inventing codes in advance of
    that.
    """
    profile = repository.get_cost_profile(conn, scope, order_id)

    # A component is known when it holds a value, including an explicitly
    # saved zero (US-008's own definition, walmart-user-stories.md:346-352)
    # -- buying_cost_total is None only when the per-unit cost was never
    # entered at all, never when it was entered as zero.
    missing = [] if profile["buying_cost_total"] is not None else [_BUYING_COST_COMPONENT_CODE]

    return {
        "complete": not missing,
        "missing_component_codes": missing,
    }
