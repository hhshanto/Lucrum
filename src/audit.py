"""Audit event writing.

Used by the first endpoint rather than added later. An audit trail retrofitted
after the fact has a hole precisely where the early mistakes are, and those are
the ones worth being able to explain.

``operation_id`` carries the idempotency guarantee required by US-014 AC-03: a
write whose outcome is unknown -- a timeout, a dropped connection, an impatient
second click -- can be replayed with the same id and will take effect once.
The uniqueness constraint on the column is what enforces it, so the guarantee
survives concurrent retries rather than depending on a check-then-insert race.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

__all__ = ["AuditEvent", "record", "new_operation_id", "DuplicateOperation"]


OPERATION_ID_CONSTRAINT = "audit_events_operation_id_unique"


class DuplicateOperation(Exception):
    """This operation_id has already been recorded.

    Not an error condition in itself. It means a retry reached a write that
    already succeeded, and the caller should return the original outcome rather
    than acting a second time.

    ``existing_event`` carries the row that was already written, so the caller
    can genuinely return the original outcome. An earlier version reported a
    nil UUID here, which was a fabricated identifier -- the same failure as
    coercing an unknown cost to zero, applied to a primary key.
    """

    def __init__(self, operation_id: str, existing_event: dict[str, Any]) -> None:
        super().__init__(
            f"operation {operation_id} already recorded as {existing_event['id']}"
        )
        self.operation_id = operation_id
        self.existing_event = existing_event

    @property
    def existing_id(self) -> uuid.UUID:
        return self.existing_event["id"]


def new_operation_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    entity_type: str
    operation_id: str
    entity_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    reason: str | None = None


def _json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    # default=str so Decimal and datetime survive. Decimals serialise as their
    # exact string form, never as a float -- a float in the audit trail would
    # misreport the very figure the trail exists to explain.
    return json.dumps(value, default=str, sort_keys=True)


def record(conn: psycopg.Connection, event: AuditEvent) -> uuid.UUID:
    """Append an audit event. Raises DuplicateOperation on replay.

    Must run inside the same transaction as the change it describes. An audit
    row committed separately from its change can outlive a rollback and
    describe something that never happened.
    """
    # The insert runs inside a savepoint. Without one, a unique violation
    # aborts the whole transaction and the re-query below is impossible -- the
    # caller would be told "already recorded" and be unable to find what was
    # recorded. The savepoint scopes the failure so recovery can proceed.
    try:
        with conn.transaction():
            row = conn.execute(
                """
                insert into audit_events (
                    operation_id, actor_id, store_id, entity_type, entity_id,
                    event_type, before_state, after_state, reason
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    event.operation_id,
                    event.actor_id,
                    event.store_id,
                    event.entity_type,
                    event.entity_id,
                    event.event_type,
                    _json(event.before_state),
                    _json(event.after_state),
                    event.reason,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        # Only the operation_id constraint means "already done". Any other
        # unique violation is a real error, and reporting it as a successful
        # idempotent replay would swallow it.
        constraint = getattr(exc.diag, "constraint_name", None)
        if constraint != OPERATION_ID_CONSTRAINT:
            raise
        existing = find_by_operation(conn, event.operation_id)
        if existing is None:  # pragma: no cover - would mean the row vanished
            raise
        raise DuplicateOperation(event.operation_id, existing) from exc

    return row["id"]


def find_by_operation(conn: psycopg.Connection, operation_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "select * from audit_events where operation_id = %s", (operation_id,)
    ).fetchone()
