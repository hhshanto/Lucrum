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


def require_can_create_store(role: str) -> None:
    """BR-11: creating a store is Owner-only."""
    if role not in _CAN_CREATE_STORE:
        raise Forbidden("role does not permit creating a store")


def require_can_change_store_settings(role: str) -> None:
    """BR-11: changing store settings is Owner or Admin, never Manager."""
    if role not in _CAN_CHANGE_STORE_SETTINGS:
        raise Forbidden("role does not permit changing store settings")


def create_store(
    conn: psycopg.Connection,
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
                    # *creation*. store_created follows the same naming
                    # convention; the Owner has not yet confirmed it.
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
        # The attempted insert above was rolled back along with the audit
        # insert that detected the replay (see module docstring) -- nothing
        # from this call's payload was persisted. Owner scope is correct
        # here regardless of which actor is replaying: only the Owner can
        # reach this function at all (the require_can_create_store check
        # above already enforced it), and the Owner's scope covers every
        # store unconditionally.
        existing = repository.get_store(
            conn, StoreScope.owner(actor_id=actor_id), replay.existing_event["entity_id"]
        )
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
    """
    require_can_change_store_settings(role)

    # Scope check up front, outside the write transaction: this both
    # produces the 404 US-002 AC-02 requires for a store this actor cannot
    # reach, and gives the "before" values the audit event below records.
    before = repository.get_store(conn, scope, store_id)

    try:
        with conn.transaction():
            after = repository.update_store_identity(conn, store_id, fields)
            audit.record(
                conn,
                audit.AuditEvent(
                    # B-12 (see create_store above): no event type for a
                    # store identity change is named by
                    # api-service-design.md either. store_renamed follows
                    # the existing naming convention; awaiting Owner
                    # confirmation.
                    event_type="store_renamed",
                    entity_type="store",
                    entity_id=store_id,
                    store_id=store_id,
                    operation_id=operation_id,
                    actor_id=actor_id,
                    before_state={key: before.get(key) for key in fields},
                    after_state=fields,
                ),
            )
    except audit.DuplicateOperation:
        # The update above was rolled back with the audit insert that
        # detected the replay -- the second payload never applied. Re-fetch
        # under the same scope to report the (unchanged) current state.
        current = repository.get_store(conn, scope, store_id)
        return current, True

    return after, False
