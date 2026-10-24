"""Store business rules: creation and identity changes. Module: Stores.

US-003 -- Create and configure a store. Implements AC-01 (stable identifier),
AC-03 (the capability matrix), and AC-04 (rename preserves identity and
links). AC-02 (missing configuration is identified, not guessed) is already
satisfied by ``repository.activation_blockers``, reused here unchanged.

BR-11 / the capability matrix (``walmart-user-stories.md:665-695``) draws
*two different lines*, and this module enforces them as two separate checks
-- never folded into one "privileged role" bucket. That fold is exactly the
trap AC-03's own test suite is written to catch: "Create store" is Owner
only (Admin is explicitly NOT included, unlike the MFA-required bucket in
``access.service._PRIVILEGED_ROLES`` which *does* include Admin); "Change
store settings" is Owner and Admin.

Role (capability) and store scope (assignment) are different questions and
are raised as different exceptions, never merged into one status code:

    Forbidden   -- the actor's role does not permit the action at all (403).
                   Checked first, with no database read beyond the role
                   already resolved at sign-in -- a Manager who holds a
                   genuine assignment to the store still gets 403, not 404
                   (the store is not a secret from them; the action is not
                   permitted).
    OutOfScope  -- the store is not assigned to this actor, or does not
                   exist at all (404) -- enforced by ``repository.get_store``,
                   raised only once the role check has already passed. An
                   Admin whose role permits the action but who holds no
                   assignment to this particular store still gets 404, not
                   403 (the matrix grants the capability; access-v1 still
                   requires the assignment).

Every write here is idempotent on ``operation_id`` (AGENTS.md: "every
financial write: ... operation_id, audit event. Every one, every time" --
applied here even though a store record itself carries no money). The
pattern: the entity write and the audit write happen inside one *inner*
transaction (a savepoint, since ``main.connection()`` already holds the
outer one). If the audit insert discovers the operation_id was already used,
``audit.DuplicateOperation`` propagates out of that inner transaction block,
which rolls back the inner savepoint -- undoing the entity write along with
it -- before this module catches the exception and returns the *original*
outcome instead. Without that savepoint boundary, a replay carrying a
different payload would insert a second, orphaned row before the audit
table's uniqueness constraint ever had a chance to object.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

from src import audit
from src.scoping import Forbidden, StoreScope
from src.stores import repository

__all__ = [
    "require_can_create_store",
    "require_can_change_store_settings",
    "require_can_change_managed_from",
    "create_store",
    "update_store_identity",
]


# BR-11's capability matrix is a closed, spec-given rule -- not a tunable
# policy value like an edit limit or a timeout. The same kind of structural
# constant already exists for MFA enforcement
# (``access.service._PRIVILEGED_ROLES``), which this deliberately does NOT
# reuse: that set answers "which roles must complete MFA" (Owner, Admin) --
# a different question from either line of the capability matrix below, and
# conflating them is specifically how an Admin would wrongly gain the
# ability to create a store.
_CAN_CREATE_STORE = frozenset({"owner"})
_CAN_CHANGE_STORE_SETTINGS = frozenset({"owner", "admin"})

# B-13 (US-003 audit, F-26): the capability matrix (walmart-user-stories.md
# :671) reads "Change store settings: Owner yes, Admin yes", which taken on
# its own would let an Admin move managed_from. Three other passages
# disagree: walmart-user-stories.md:171 says managed_from is "set explicitly
# by the Owner"; database-schema.md:193 says "Owner may change it, audited";
# and walmart-user-stories.md:693 denies Admin the power to "re-post an order
# into or out of a protected period" -- which is exactly the effect of moving
# managed_from across the pre_onboarding boundary (BR-14, AGENTS.md #8: a
# pre_onboarding order is never posted forward, and moving managed_from is
# the one action that could change which orders fall on which side of that
# line). Four passages, three of which name the Owner specifically and one
# of which forbids Admin the equivalent power over a different boundary; the
# generic matrix cannot be read to override all three on its own.
#
# Interim handling (Owner decision, not invented here): managed_from_year
# and managed_from_month are Owner-only on PATCH, pending an explicit Owner
# ruling on the conflict. This is the intersection of every reading above --
# every document agrees the Owner may; only the generic settings row
# arguably extends the power to Admin, and a narrower reading cannot be
# wrong in the direction of granting a power no document actually grants.
_CAN_CHANGE_MANAGED_FROM = frozenset({"owner"})
_MANAGED_FROM_FIELDS = frozenset({"managed_from_year", "managed_from_month"})


def require_can_create_store(role: str) -> None:
    """BR-11: creating a store is Owner-only."""
    if role not in _CAN_CREATE_STORE:
        raise Forbidden("role does not permit creating a store")


def require_can_change_store_settings(role: str) -> None:
    """BR-11: changing store settings is Owner or Admin, never Manager."""
    if role not in _CAN_CHANGE_STORE_SETTINGS:
        raise Forbidden("role does not permit changing store settings")


def require_can_change_managed_from(role: str) -> None:
    """B-13 (interim, see the module-level comment above): moving
    managed_from is Owner-only, not Owner-or-Admin, until the conflict
    between the capability matrix and walmart-user-stories.md:171/:693 and
    database-schema.md:193 is resolved by the Owner."""
    if role not in _CAN_CHANGE_MANAGED_FROM:
        raise Forbidden("role does not permit changing managed_from (B-13, interim)")


def _event_type_for_update(fields: dict[str, Any]) -> str:
    """Which audit event type a PATCH should emit (F-27).

    api-service-design.md names these explicitly: ``:1195``
    ``store_timezone_changed`` for a timezone change specifically -- carved
    out on its own because moving a store's timezone moves every month
    boundary resolved against it, which a cosmetic rename or a currency
    correction does not -- and ``:1188`` ``store_settings_updated`` for the
    general case. Both are spec-named, so B-12 (the gap for *creation*,
    where no event type is named at all) does not apply to either branch
    here; see ``create_store`` below for the genuinely-unspecified case.

    A PATCH that changes timezone *and* something else still reports
    ``store_timezone_changed`` -- the timezone move is the more significant
    fact and must not be lost inside a generic "settings updated" entry.
    """
    if "timezone" in fields:
        return "store_timezone_changed"
    return "store_settings_updated"


def create_store(
    conn: psycopg.Connection,
    scope: StoreScope,
    *,
    actor_id: uuid.UUID | None,
    role: str,
    fields: dict[str, Any],
    operation_id: str,
) -> tuple[dict[str, Any], bool]:
    """Register a new store. Returns ``(store_row, replayed)``.

    AC-01: the identifier returned and persisted is the database-assigned
    primary key (``gen_random_uuid()``, 001_baseline.sql) -- never derived
    from ``fields["name"]`` -- so it is stable across a later rename and
    distinct from the display name by construction, not by convention.
    """
    require_can_create_store(role)

    try:
        with conn.transaction():
            store = repository.create_store(conn, **fields)
            audit.record(
                conn,
                audit.AuditEvent(
                    # B-12: api-service-design.md:1186-1209 names
                    # store_settings_updated, store_timezone_changed and
                    # store_assignment_changed, but no event type for store
                    # *creation* specifically. store_created follows the
                    # same naming convention; the Owner has not yet
                    # confirmed it. (This is the one genuinely-unspecified
                    # case -- contrast update_store_identity below, where
                    # the spec does name both event types and B-12 does not
                    # apply; F-27, US-003 audit.)
                    event_type="store_created",
                    entity_type="store",
                    entity_id=store["id"],
                    store_id=store["id"],
                    operation_id=operation_id,
                    actor_id=actor_id,
                    after_state=fields,
                ),
            )
    except audit.DuplicateOperation as replay:
        # F-31 (US-003 audit): a DuplicateOperation alone does not mean this
        # call is a replay of *this* request -- the same Idempotency-Key
        # could have been spent on an entirely different write (a note, a
        # rename). Confirm the existing event really is a store_created
        # event before trusting its entity_id as "the store this call
        # created"; otherwise this would hand back an unrelated entity
        # under a 200/201 that looks exactly like success.
        audit.check_replay_target(
            replay.existing_event, event_type="store_created", entity_type="store"
        )
        # The attempted insert above was rolled back along with the audit
        # insert that detected the replay (see module docstring) -- nothing
        # from this call's payload was persisted. F-34: the actor's own
        # resolved scope is used here, not a fabricated Owner scope -- sound
        # today only because require_can_create_store already restricted
        # this function to the Owner, but a fabricated scope would become a
        # live escalation the moment that restriction ever widens.
        existing = repository.get_store(conn, scope, replay.existing_event["entity_id"])
        return existing, True

    return store, False


def update_store_identity(
    conn: psycopg.Connection,
    scope: StoreScope,
    *,
    actor_id: uuid.UUID | None,
    role: str,
    store_id: uuid.UUID,
    fields: dict[str, Any],
    operation_id: str,
) -> tuple[dict[str, Any], bool]:
    """Change one or more identity fields on an existing store (rename,
    retimezone, recurrency, re-set ``managed_from``). Returns
    ``(store_row, replayed)``.

    AC-03: role is checked *before* the store is even looked up, so a
    Manager who genuinely holds the assignment still gets 403 (not 404) --
    the role check alone is enough to reject, with no scope-dependent
    information in the decision at all. AC-04: ``repository.get_store`` is
    the same scoped read used by the read endpoints, so an out-of-scope or
    nonexistent store answers identically here (404) as it does on a plain
    GET.

    B-9 (open): whether an inactive store accepts writes at all is not
    settled here -- this function carries no ``is_active`` filter, exactly
    like ``repository.update_store_identity`` it calls into. See
    ``repository.list_stores``'s docstring for the read-path equivalent of
    this same open question.
    """
    require_can_change_store_settings(role)

    # B-13 (interim, module-level comment above): a per-field capability
    # check layered on top of the general one -- Owner/Admin may change
    # store settings, but moving managed_from specifically is Owner-only
    # until the matrix-vs-:171/:193/:693 conflict is resolved (F-26).
    if _MANAGED_FROM_FIELDS & fields.keys():
        require_can_change_managed_from(role)

    # Scope check up front, outside the write transaction: this both
    # produces the 404 US-002 AC-02 requires for a store this actor cannot
    # reach, and gives the "before" values the audit event below records.
    before = repository.get_store(conn, scope, store_id)

    # F-39: an empty patch changes nothing. Treating it as a write would
    # record a store_settings_updated event with after_state={} describing
    # a rename that never happened, and would bump updated_at for no reason
    # -- a no-op must stay a no-op, not a fabricated audit entry.
    if not fields:
        return before, False

    event_type = _event_type_for_update(fields)

    try:
        with conn.transaction():
            after = repository.update_store_identity(conn, scope, store_id, fields)
            audit.record(
                conn,
                audit.AuditEvent(
                    # F-27 (US-003 audit): api-service-design.md DOES name
                    # identity-change events -- :1188 store_settings_updated
                    # and :1195 store_timezone_changed specifically, so B-12
                    # (a genuine gap) does not apply here at all; it applies
                    # only to store_created above, where the spec truly
                    # names nothing. event_type is picked by
                    # _event_type_for_update so a timezone change is never
                    # recorded under the generic type -- the two answer
                    # different questions for anyone reading the audit log
                    # later (BR-14: a timezone move shifts every month
                    # boundary; a cosmetic rename does not).
                    event_type=event_type,
                    entity_type="store",
                    entity_id=store_id,
                    store_id=store_id,
                    operation_id=operation_id,
                    actor_id=actor_id,
                    before_state={key: before.get(key) for key in fields},
                    after_state=fields,
                ),
            )
    except audit.DuplicateOperation as replay:
        # F-31 (US-003 audit): confirm this operation_id was actually spent
        # on *this* store, under *this* kind of change, before trusting it
        # as a replay. A key reused against a different store, or against a
        # different endpoint/event type entirely, is a collision -- the
        # earlier write must not be reported back as if it were this
        # request's own outcome.
        audit.check_replay_target(
            replay.existing_event,
            event_type=event_type,
            entity_type="store",
            entity_id=store_id,
        )
        # The update above was rolled back with the audit insert that
        # detected the replay -- the second payload never applied. Re-fetch
        # under the same scope to report the (unchanged) current state.
        current = repository.get_store(conn, scope, store_id)
        return current, True

    return after, False
