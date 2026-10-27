"""Business rules for granting and revoking a store manager assignment.

US-004 -- "Assign managers to stores to stores" (capability matrix,
``walmart-user-stories.md:670``) is Owner yes / Admin no / Manager no --
the *same shape* as "Create store" (``:668``), with no per-store scope
dependency at all (``src/stores/service.py::require_can_create_store``).
This module deliberately does NOT reuse
``stores_service.require_can_change_store_settings`` (Owner-or-Admin): that
answers a *different* row of the matrix ("Change store settings"), and
reusing it here would wrongly let an Admin assign managers too (the exact
trap ``TestRoleRejectionOrdering::test_admin_cannot_grant_even_though...``
is written to catch).

Role (capability) and store scope/existence are different questions, raised
as different exceptions, never merged into one status code -- the same
discipline ``src/stores/service.py`` documents at length:

    Forbidden   -- the actor's role does not permit this action at all
                   (403). Checked first, with no database read at all, so a
                   Manager or Admin who genuinely holds the assignment, or
                   is targeting a store_id that does not even exist, gets
                   the identical 403 either way.
    OutOfScope  -- the store does not exist (404, via
                   ``stores.repository.get_store`` -- reused rather than
                   re-implemented, so "does this store exist" never
                   disagrees with the answer every other endpoint gives).
                   Only reached once the role check has already passed:
                   the Owner is the only actor who reaches it at all.

Every write here is idempotent on ``operation_id``, following the exact
mechanics ``src/stores/service.py`` establishes (and the F-55 lesson that
mechanics document records at length): the operation ledger
(``audit.find_by_operation``) is checked BEFORE any uniqueness/existence
precondition, so an honest retry under the same ``Idempotency-Key`` is
always recognised as a replay before it can be mistaken for a fresh,
conflicting attempt. A genuinely fresh key aimed at an already-assigned
pair is a different, named conflict (``AlreadyAssigned``, 409) -- never a
raw ``IntegrityError`` and never a silent second success.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

from src import audit
from src.access import repository as access_repository
from src.scoping import Forbidden, StoreScope
from src.stores import repository as stores_repository
from src.team import repository as team_repository

__all__ = [
    "require_can_manage_store_assignments",
    "UserNotFound",
    "AlreadyAssigned",
    "list_managers",
    "grant_manager",
    "revoke_manager",
]


# BR-11's capability matrix is a closed, spec-given rule, not a tunable
# policy value -- the same reasoning ``stores.service._CAN_CREATE_STORE``
# documents. A fresh constant, not an alias of any store-settings constant:
# aliasing is exactly how an Admin would wrongly gain this power.
_CAN_MANAGE_STORE_ASSIGNMENTS = frozenset({"owner"})

_EVENT_TYPE = "store_assignment_changed"
_ENTITY_TYPE = "user_store_assignment"


class UserNotFound(Exception):
    """The ``user_id`` being granted does not reference any ``users`` row.

    A plain bad reference, not a store-scope question -- translated to a
    named 404 rather than left to surface as a raw foreign-key
    ``IntegrityError`` (the same discipline ``stores.service.DuplicateStoreKey``
    applies to a conflicting ``walmart_store_key``).
    """

    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__(f"user {user_id} does not exist")
        self.user_id = user_id


class AlreadyAssigned(Exception):
    """This (user_id, store_id) pair is already assigned, under a *fresh*
    ``Idempotency-Key`` -- a genuine conflict, never a silent success and
    never a raw ``UniqueViolation`` leaking out as a 500."""

    def __init__(self, user_id: uuid.UUID, store_id: uuid.UUID) -> None:
        super().__init__(f"user {user_id} is already assigned to store {store_id}")
        self.user_id = user_id
        self.store_id = store_id


def require_can_manage_store_assignments(role: str) -> None:
    """BR-11: assigning or removing a Manager's store assignment is
    Owner-only."""
    if role not in _CAN_MANAGE_STORE_ASSIGNMENTS:
        raise Forbidden("role does not permit managing store assignments")


def _assignment_payload(*, user_id: uuid.UUID, store_id: uuid.UUID) -> dict[str, Any]:
    return {"user_id": str(user_id), "store_id": str(store_id)}


def _check_assignment_replay_target(
    existing_event: dict[str, Any],
    *,
    action: str,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
) -> None:
    """Confirm a ``DuplicateOperation`` is genuinely a replay of *this*
    grant/revoke, not an ``Idempotency-Key`` collision with something else
    (the F-31 discipline, extended here with a check
    ``audit.check_replay_target`` does not itself make: ``store_id`` and the
    grant/revoke discriminator are not part of its generic signature, so
    they are checked here alongside it).
    """
    audit.check_replay_target(
        existing_event,
        event_type=_EVENT_TYPE,
        entity_type=_ENTITY_TYPE,
        entity_id=user_id,
    )
    after = existing_event.get("after_state") or {}
    if existing_event["store_id"] != store_id or after.get("action") != action:
        raise audit.ReplayConflict(
            existing_event.get("operation_id", "<unknown>"), existing_event, _EVENT_TYPE
        )


def list_managers(
    conn: psycopg.Connection, scope: StoreScope, store_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every user currently assigned to a store the caller may see.

    ``OutOfScope`` (404) for a store outside the caller's scope or one that
    does not exist, same as every other read on a store.
    """
    stores_repository.get_store(conn, scope, store_id)
    rows = team_repository.list_assignments(conn, store_id=store_id)
    return [_assignment_payload(user_id=r["user_id"], store_id=r["store_id"]) for r in rows]


def grant_manager(
    conn: psycopg.Connection,
    scope: StoreScope,
    *,
    actor_id: uuid.UUID | None,
    role: str,
    store_id: uuid.UUID,
    user_id: uuid.UUID,
    operation_id: str,
) -> tuple[dict[str, Any], bool]:
    """Assign a user to a store. Returns ``(assignment, replayed)``.

    AC-01/AC-02: the write is a plain row insert into
    ``user_store_assignments`` -- the same table ``resolve_scope`` reads --
    so a granted Manager reaches the store on their very next request, and
    the same user granted to a second store gains access to both without any
    second ``users`` row ever being created.
    """
    require_can_manage_store_assignments(role)

    # Role rejected above with no store lookup at all. Only the Owner
    # reaches this line, so this 404 can never be confused with the role
    # rejection above (TestRoleRejectionOrdering).
    stores_repository.get_store(conn, scope, store_id)

    # F-55 (US-003 audit), re-applied here: resolve operation_id BEFORE any
    # uniqueness precondition. An honest retry resends the identical
    # (user_id, store_id) under the identical Idempotency-Key, and that pair
    # is necessarily already assigned from the first call -- checking
    # "already assigned" first would punish the honest retry as a conflict.
    existing_event = audit.find_by_operation(conn, operation_id)
    if existing_event is not None:
        _check_assignment_replay_target(
            existing_event, action="granted", user_id=user_id, store_id=store_id
        )
        return _assignment_payload(user_id=user_id, store_id=store_id), True

    user = access_repository.get_user(conn, user_id)
    if user is None:
        raise UserNotFound(user_id)

    if team_repository.assignment_exists(conn, user_id=user_id, store_id=store_id):
        # A genuinely fresh key aimed at a pair that is already assigned --
        # not a replay (handled above), a real conflict.
        raise AlreadyAssigned(user_id, store_id)

    try:
        with conn.transaction():
            try:
                team_repository.create_assignment(conn, user_id=user_id, store_id=store_id)
            except psycopg.errors.UniqueViolation as exc:
                # Concurrency backstop: two fresh keys raced past the
                # assignment_exists check above simultaneously. Only the
                # assignment table's own PK constraint means "already
                # assigned" -- anything else is a real, unanticipated error.
                constraint = getattr(exc.diag, "constraint_name", None)
                if constraint != team_repository.ASSIGNMENT_PK_CONSTRAINT:
                    raise
                raise AlreadyAssigned(user_id, store_id) from exc
            audit.record(
                conn,
                audit.AuditEvent(
                    event_type=_EVENT_TYPE,
                    entity_type=_ENTITY_TYPE,
                    entity_id=user_id,
                    store_id=store_id,
                    operation_id=operation_id,
                    actor_id=actor_id,
                    after_state={
                        "action": "granted",
                        "user_id": str(user_id),
                        "store_id": str(store_id),
                    },
                ),
            )
    except audit.DuplicateOperation as replay:
        # Concurrency backstop: a racer committed between the pre-check
        # above and this call's own insert. The attempted insert was rolled
        # back along with the audit insert that detected the replay.
        _check_assignment_replay_target(
            replay.existing_event, action="granted", user_id=user_id, store_id=store_id
        )
        return _assignment_payload(user_id=user_id, store_id=store_id), True

    return _assignment_payload(user_id=user_id, store_id=store_id), False


def revoke_manager(
    conn: psycopg.Connection,
    scope: StoreScope,
    *,
    actor_id: uuid.UUID | None,
    role: str,
    store_id: uuid.UUID,
    user_id: uuid.UUID,
    operation_id: str,
) -> tuple[dict[str, Any], bool]:
    """Remove a user's assignment to a store. Returns ``(assignment, replayed)``.

    AC-03: the row disappears from ``user_store_assignments`` inside this
    one write, so the very next ``resolve_scope`` call for that user -- not
    merely their next sign-in -- no longer includes this store.

    Revoking a pair that was never assigned is not specified by any AC or
    test; handled here as a no-op (200, nothing recorded) rather than a
    manufactured error, so it can never 500.
    """
    require_can_manage_store_assignments(role)

    stores_repository.get_store(conn, scope, store_id)

    existing_event = audit.find_by_operation(conn, operation_id)
    if existing_event is not None:
        _check_assignment_replay_target(
            existing_event, action="revoked", user_id=user_id, store_id=store_id
        )
        return _assignment_payload(user_id=user_id, store_id=store_id), True

    if not team_repository.assignment_exists(conn, user_id=user_id, store_id=store_id):
        # Nothing to revoke. A no-op is not an audited change -- recording
        # one here would describe a revocation that never happened (the
        # same reasoning stores.service.update_store_identity applies to an
        # empty patch).
        return _assignment_payload(user_id=user_id, store_id=store_id), False

    try:
        with conn.transaction():
            team_repository.delete_assignment(conn, user_id=user_id, store_id=store_id)
            audit.record(
                conn,
                audit.AuditEvent(
                    event_type=_EVENT_TYPE,
                    entity_type=_ENTITY_TYPE,
                    entity_id=user_id,
                    store_id=store_id,
                    operation_id=operation_id,
                    actor_id=actor_id,
                    after_state={
                        "action": "revoked",
                        "user_id": str(user_id),
                        "store_id": str(store_id),
                    },
                ),
            )
    except audit.DuplicateOperation as replay:
        _check_assignment_replay_target(
            replay.existing_event, action="revoked", user_id=user_id, store_id=store_id
        )
        return _assignment_payload(user_id=user_id, store_id=store_id), True

    return _assignment_payload(user_id=user_id, store_id=store_id), False
